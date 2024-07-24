import hashlib
import json
import os
import pickle
import pandas as pd
import requests
import logging
from scapy.all import IP, TCP, UDP, ICMP, Ether, sniff, raw
from confluent_kafka import Producer, Consumer, KafkaError
from confluent_kafka.admin import AdminClient
import threading
import time
from decimal import Decimal
from flask import Flask, jsonify, request
import threading
from sklearn.preprocessing import RobustScaler

KAFKA_BROKER = os.getenv('KAFKA_BROKER', 'kafka:9092')
RAW_TOPIC = 'raw_data'
PROCESSED_TOPIC = 'processed_data'
PREDICTIONS_TOPIC = 'predictions'
MODEL_PATH = '/app/model/'
APP_PATH = '/app/'
SCALER_PATH = '/app/scaler_data/robust_scaler.pkl'
TORCHSERVE_URL = "http://torchserve:8080"

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s',
                    handlers=[logging.StreamHandler()])

if os.path.exists(MODEL_PATH):
    logging.info(f"App directory contents: {os.listdir(APP_PATH)}")
    logging.info(f"Model directory contents: {os.listdir(MODEL_PATH)}")


##################################################
# KAFKA UTILITY
##################################################

# Uses AdminClient to verify the existence of a topic
def topic_exists(broker_address, topic_name):
    try:
        admin_client = AdminClient({'bootstrap.servers': broker_address})
        metadata = admin_client.list_topics(timeout=10)
        return topic_name in metadata.topics
    except Exception as e:
        logging.error(f"Error checking topic existence: {e}")
        return False

producer_config = {
    'bootstrap.servers': KAFKA_BROKER
}

consumer_config = {
    'bootstrap.servers': KAFKA_BROKER,
    'group.id': 'network_data',
    'auto.offset.reset': 'earliest'
}


##################################################
# TRAFFIC CAPTURE - thread 1
##################################################

# The network traffic takes place on host machine, which writes in real time to a file
# This function then reads that file and passes them to the producer for raw data topic
def capture_network_traffic_from_file(file_path, broker_address):
    logging.info(f"Thread 1: Connecting to live capture file")
    while True:
        logging.debug(f"Thread 1: Checking if capture file exists: {file_path}")
        if not os.path.exists(file_path):
            logging.error(f"Thread 1: File not found: {file_path}. Retrying in 5 seconds...")
            time.sleep(5)
            continue

        logging.debug(f"Thread 1: Capturing network traffic from file: {file_path}")
        try:
            packets = sniff(offline=file_path, count=1000)
            if packets:
                logging.debug(f"Thread 1: Captured {len(packets)} packets")
                produce_raw_data(packets, broker_address)
            else:
                logging.info("Thread 1: No packets captured. Sleeping before retrying...")
                time.sleep(5)  # Wait before trying again
        except FileNotFoundError:
            logging.error(f"Thread 1: File not found: {file_path}. Retrying in 5 seconds...")
            time.sleep(5)
        except Exception as e:
            logging.error(f"Thread 1: Error capturing traffic: {e}. Retrying in 5 seconds...")
            time.sleep(5)

# Utility function to serialize the packets
def serialize_packet(packet):
    try:
        serialized = {
            "time": packet.time,
            "data": raw(packet).hex()
        }
        logging.debug(f"Thread 1: Serialized packet: {serialized}")
        return serialized
    except Exception as e:
        logging.error(f"Thread 1: Error serializing packet: {e}")
        return None

# Produces the raw data packets from capture to the raw data topic
def produce_raw_data(packets, broker_address):
    logging.debug("Thread 1: Connecting to {}".format(RAW_TOPIC))
    while not topic_exists(broker_address, RAW_TOPIC):
        logging.warning(f"Thread 1: {RAW_TOPIC} topic not found. Sleeping for 10 seconds and retrying...")
        time.sleep(10)
    logging.debug("Thread 1: Connected to {}".format(RAW_TOPIC))

    producer = Producer(producer_config)
    try:
        for packet in packets:
            serialized_packet = serialize_packet(packet)
            if serialized_packet:
                producer.produce(
                    topic="raw_data",
                    key=str(packet.time),
                    value=json.dumps(serialized_packet, cls=CustomEncoder)
                )
        producer.flush()
        logging.debug("Thread 1: Finished producing raw data to Kafka")
    except Exception as e:
        logging.error(f"Thread 1: Failed to produce raw data: {e}")


##################################################
# PACKET PROCESSING - thread 2
##################################################

class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super(CustomEncoder, self).default(obj)

def fit_scaler(broker_address, sample_size=1000):
    consumer_config = {
        'bootstrap.servers': broker_address,
        'group.id': 'scaler_fitting',
        'auto.offset.reset': 'earliest'
    }
    consumer = Consumer(consumer_config)
    consumer.subscribe([RAW_TOPIC])
    
    features_list = []
    while len(features_list) < sample_size:
        msg = consumer.poll(1.0)
        if msg is None:
            continue
        if msg.error():
            continue
        
        value = json.loads(msg.value())
        features = process_packet(value)
        if features:
            features_list.append(features)
    
    consumer.close()
    
    df = pd.DataFrame(features_list, columns=['src_ip', 'src_port', 'dst_ip', 'dst_port', 'protocol', 'flags'])
    scaler = RobustScaler()
    scaler.fit(df)
    return scaler, df

def update_scaler(scaler, new_data, current_data):
    combined_data = pd.concat([current_data, new_data], ignore_index=True)
    updated_scaler = RobustScaler()
    updated_scaler.fit(combined_data)
    return updated_scaler, combined_data

def process_raw_data(broker_address):
    logging.info("Thread 2: Connecting to {}".format(RAW_TOPIC))
    while not topic_exists(broker_address, RAW_TOPIC):
        logging.warning("Thread 2: {} topic not found. Sleeping for 10 seconds and retrying...".format(RAW_TOPIC))
        time.sleep(10)
    logging.info("Thread 2: Connected to {}".format(RAW_TOPIC))

    consumer_config = {
        'bootstrap.servers': broker_address,
        'group.id': 'network_data',
        'auto.offset.reset': 'earliest'
    }

    producer_config = {
        'bootstrap.servers': broker_address
    }

    consumer = Consumer(consumer_config)
    producer = Producer(producer_config)
    
    # Fit the scaler on initial sample of data
    initial_sample_size = 1000
    scaler, current_data = fit_scaler(broker_address, sample_size=initial_sample_size)
    logging.info("Thread 2: Scaler fitted on initial sample data")

    messages_processed = 0
    refit_threshold = initial_sample_size * 10
    new_data = []

    try:
        consumer.subscribe([RAW_TOPIC])
        while True:
            msg = consumer.poll(10)
            if msg is None:
                continue
            elif msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                else:
                    logging.error("Thread 2: Error consuming message: {}".format(msg.error()))
            else:
                key = msg.key().decode("utf8")
                value = json.loads(msg.value())
                offset = msg.offset()
                partition = msg.partition()
                logging.debug("Thread 2: Consumed message with key: {}, value: {}".format(key, value))

                features = process_packet(value)
                if features:
                    try:
                        logging.debug("Thread 2: Extracted features: {}".format(features))
                        # Ensure the DataFrame only includes the relevant columns
                        ordered_columns = ['src_ip', 'src_port', 'dst_ip', 'dst_port', 'protocol', 'flags']
                        df = pd.DataFrame([features], columns=ordered_columns)
                        
                        # Transform the features using the current scaler
                        scaled_features = scaler.transform(df)
                        processed_value = scaled_features[0].tolist()
                        logging.debug("Thread 2: Scaled features: {}".format(processed_value))
                        producer.produce(
                            topic=PROCESSED_TOPIC,
                            key=key,
                            value=json.dumps(processed_value, cls=CustomEncoder),
                        )
                        producer.flush()
                        consumer.commit(msg)
                        logging.debug("Thread 2: Produced processed packet to {} topic".format(PROCESSED_TOPIC))

                        # Update counters and collect new data for potential refitting
                        messages_processed += 1
                        new_data.append(features)

                        # Check if it's time to refit the scaler
                        if messages_processed % refit_threshold == 0:
                            logging.info("Thread 2: Refitting scaler with new data")
                            new_data_df = pd.DataFrame(new_data, columns=ordered_columns)
                            scaler, current_data = update_scaler(scaler, new_data_df, current_data)
                            new_data = []  # Reset new_data after refitting
                            logging.info("Thread 2: Scaler refitted successfully")

                    except Exception as e:
                        logging.error("Thread 2: Failed to process packet: {}".format(e))
    except Exception as e:
        logging.error("Thread 2: Failed to consume or produce messages: {}".format(e))
    finally:
        consumer.close()
        producer.flush()

def scale_features(features, scaler):
    try:
        scaled = scaler.fit_transform([features])[0]
        logging.debug(f"Thread 2: Scaled features: {scaled}")
        return scaled
    except Exception as e:
        logging.error(f"Thread 2: Error scaling features: {e}")
        return None

def process_packet(packet_summary):
    def ip_to_hash(ip: str) -> int:
        return int(hashlib.sha256(ip.encode()).hexdigest()[:8], 16)

    def flags_to_int(flags: str) -> int:
        return sum((0x01 << i) for i, f in enumerate('FSRPAUEC') if f in flags)

    def protocol_to_int(proto: int) -> int:
        protocol_map = {1: 1, 6: 2, 17: 3, 2: 4}  # ICMP, TCP, UDP, IGMP
        return protocol_map.get(proto, 0)

    try:
        packet = Ether(bytes.fromhex(packet_summary["data"]))
        logging.debug("Thread 2: Created Ether packet: {}".format(packet.summary()))
    except Exception as e:
        logging.error("Thread 2: Error converting packet summary to Ether object: {}".format(e))
        return None

    if IP in packet:
        ip_layer = packet[IP]
        tcp_layer = packet[TCP] if TCP in packet else None

        features = {
            'src_ip': ip_to_hash(ip_layer.src) if ip_layer else ip_to_hash("0.0.0.0"),
            'dst_ip': ip_to_hash(ip_layer.dst) if ip_layer else ip_to_hash("0.0.0.0"),
            'protocol': protocol_to_int(ip_layer.proto) if ip_layer else 0,
            'flags': flags_to_int(tcp_layer.flags) if tcp_layer and hasattr(tcp_layer, 'flags') else 0,
            'src_port': tcp_layer.sport if tcp_layer else 0,
            'dst_port': tcp_layer.dport if tcp_layer else 0
        }

        logging.debug("Thread 2: Extracted features: {}".format(features))
        return features
    else:
        logging.debug("Thread 2: Non-IP packet received, cannot process")
        return None

##################################################
# MODEL - thread 3
##################################################

# Training and loading the model

def train_model_process(start_date, end_date):
    global model_training
    logging.info(f"Starting model training process for data between {start_date} and {end_date}")

    try:
        # 1. Fetch data from Kafka
        data = fetch_data_from_kafka(start_date, end_date)
        logging.info(f"Fetched {len(data)} records from Kafka")

        # 2. Train the model and set to inference mode
        if train_and_set_inference_mode(data):

            # 3. Start prediction thread if training and setting inference mode was successful
            threading.Thread(target=prediction_thread).start()
        else:
            logging.error("Failed to train model and set to inference mode")

    except Exception as e:
        logging.error(f"Error during model training process: {str(e)}")
    finally:
        model_training = False

def fetch_data_from_kafka(start_date, end_date):
    logging.info(f"Fetching data from Kafka between {start_date} and {end_date}")
    consumer = Consumer({
        'bootstrap.servers': KAFKA_BROKER,
        'group.id': 'model_training_group',
        'auto.offset.reset': 'earliest'
    })
    consumer.subscribe([PROCESSED_TOPIC])

    data = []
    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                logging.error(f"Consumer error: {msg.error()}")
                continue

            value = json.loads(msg.value().decode('utf-8'))
            timestamp = msg.timestamp()[1]
            logging.debug(timestamp)
            if start_date <= timestamp <= end_date:
                data.append(value)
            elif timestamp > end_date:
                break

    finally:
        consumer.close()

    return data

def check_torchserve_availability():
    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = requests.get(f"{TORCHSERVE_URL}/ping")
            if response.status_code == 200:
                logging.info("TorchServe is available")
                return True
        except requests.RequestException as e:
            logging.warning(f"TorchServe not available (attempt {attempt + 1}): {e}")
        time.sleep(5)
    logging.error("TorchServe is not available after multiple attempts")
    return False

def deregister_model(model_name, version):
    try:
        response = requests.delete(f"http://torchserve:8081/models/{model_name}/{version}")
        if response.status_code == 200:
            logging.info(f"Successfully deregistered model {model_name} version {version}")
        else:
            logging.error(f"Error deregistering model {model_name} version {version}: {response.text}")
    except requests.RequestException as e:
        logging.error(f"Exception occurred while deregistering model {model_name} version {version}: {e}")

model_ready_event = threading.Event()

def train_and_set_inference_mode(data):
    if not check_torchserve_availability():
        logging.error("Cannot proceed as TorchServe is not available")
        return False

    if not train_model(data):
        logging.error("Failed to train model")
        return False

    logging.info("Attempting to set model to inference mode")
    max_retries = 300
    for attempt in range(max_retries):
        try:
            url = "http://torchserve:8081/models/memory_autoencoder"
            params = {'min_worker': '1'}
            
            response = requests.put(url, params=params)
            response.raise_for_status()
            logging.info("Model set to inference mode successfully")
            model_ready_event.set()
            return True
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                logging.error(f"Error setting model to inference mode after {max_retries} attempts: {e}")
                return False
            logging.warning(f"Error setting model to inference mode (attempt {attempt + 1}): {e}")
            time.sleep(min(30, 2 ** attempt))  # Exponential backoff with a maximum of 30 seconds

    return False

def train_model(data):
    model_name = "memory_autoencoder"
    model_version = "1.0"
    max_retries = 5

    logging.info("Attempting to train model.")
    
    # Check if the model is already registered
    try:
        response = requests.get(f"http://torchserve:8081/models/{model_name}")
        if response.status_code == 200:
            logging.info(f"Model {model_name} is registered.")
    except requests.RequestException as e:
        logging.error(f"Error checking existing model registration: {e}")
    
    for attempt in range(max_retries):
        try:
            url = f"{TORCHSERVE_URL}/predictions/memory_autoencoder"
            headers = {'X-Request-Type': 'train'}
            
            logging.info(f"Sending model training request with {data} (attempt {attempt + 1})")
            response = requests.post(url, json=data, headers=headers)
            response.raise_for_status()
            
            if response.status_code == 200:
                training_result = response.json()
                if training_result.get('status') == 'success':
                    logging.info("Model training completed successfully")
                    return True
                else:
                    logging.error(f"Model training failed: {training_result.get('message', 'Unknown error')}")
            else:
                logging.error(f"Unexpected response from TorchServe: {response.text}")
            
            return False
        except requests.RequestException as e:
            logging.error(f"Error sending model training request (attempt {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                logging.error(f"Failed to send model training request after {max_retries} attempts")
                return False
            time.sleep(min(30, 5 * (attempt + 1)))  # Linear backoff with a maximum of 30 seconds
    
    return False

# Making predictions

def wait_for_model_ready():
    logging.info("Waiting for model to be ready...")
    model_ready_event.wait()  # Block until the event is set
    logging.info("Model is ready")

def query_model(data):
    url = f"{TORCHSERVE_URL}/predictions/memory_autoencoder"
    
    logging.info(f"Querying model with data: {data}")
    try:
        response = requests.post(url, json=data)
        response.raise_for_status()
        prediction = response.json()
        logging.info(f"Received prediction {prediction} using weights from: {prediction.get('weights_file')}")
        return prediction
    except requests.exceptions.RequestException as e:
        logging.error(f"Error querying model: {e}")
        return {"error": "Failed to get prediction"}

def prediction_thread():
    logging.info("Starting prediction thread")
    
    # Wait for the model to be ready before starting predictions
    wait_for_model_ready()
    
    consumer = Consumer({
        'bootstrap.servers': KAFKA_BROKER,
        'group.id': 'prediction_group',
        'auto.offset.reset': 'latest'
    })
    consumer.subscribe([PROCESSED_TOPIC])

    producer = Producer({'bootstrap.servers': KAFKA_BROKER})

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                logging.error(f"Consumer error: {msg.error()}")
                continue

            try:
                value = json.loads(msg.value().decode('utf-8'))
                prediction = query_model(value)
                
                if "error" in prediction:
                    logging.error(f"Error in prediction: {prediction['error']}")
                    continue
                
                # Extract anomaly results and produce individual messages
                anomaly_results = prediction.get('anomaly_results', [])
                weights_file = prediction.get('weights_file')

                for result in anomaly_results:
                    packet_id = result['packet_id']
                    output = {
                        "packet_id": packet_id,
                        "reconstruction_error": result['reconstruction_error'],
                        "weights_file": weights_file
                    }

                    producer.produce(PREDICTIONS_TOPIC, key=str(packet_id), value=json.dumps(output))
                
                producer.flush()
                
                logging.info(f"Produced predictions for {len(anomaly_results)} packets")
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding message: {e}")
            except Exception as e:
                logging.error(f"Unexpected error processing message: {e}")

    except KeyboardInterrupt:
        logging.info("Interrupted. Closing consumer.")
    finally:
        consumer.close()

##################################################
# FLASK FOR MODEL TRAINING - thread 4
##################################################

model_training = False
app = Flask(__name__)

@app.route('/train_model', methods=['POST'])
def train_model_endpoint():
    logging.info(f"Model training job received")
    global model_training
    if model_training:
        return jsonify({"message": "Model training already in progress"}), 400
    model_training = True

    data = request.json
    start_date = data.get('startDate')
    end_date = data.get('endDate')

    if not start_date or not end_date:
        model_training = False
        return jsonify({"message": "Start and end dates are required"}), 400
    
    logging.info(f"Training data from {start_date} - {end_date}")

    threading.Thread(target=train_model_process, args=(start_date, end_date)).start()
    return jsonify({"message": "Model training started"}), 200

@app.route('/health', methods=['GET'])
def health_check():
    # Implement health check logic
    return jsonify({"status": "healthy"}), 200


##################################################
# MAIN
##################################################

def main():
    logging.info("Application started")
    time.sleep(10)
    try:
        broker_address = KAFKA_BROKER
    except Exception as e:
        logging.error(f"No broker address found: {e}")
        return

    threads = [
        threading.Thread(target=capture_network_traffic_from_file, args=("/mnt/capture/traffic.pcap", broker_address)),
        threading.Thread(target=process_raw_data, args=(broker_address,)),
        threading.Thread(target=prediction_thread),
        threading.Thread(target=app.run, kwargs={'host': '0.0.0.0', 'port': 5000})
    ]

    for thread in threads:
        thread.start()

    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        logging.info("Shutting down ...")
        # TO DO - Implement cleanup logic here

if __name__ == "__main__":
    main()