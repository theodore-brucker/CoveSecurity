import hashlib
import json
import os
import pandas as pd
import requests
import logging
from scapy.all import IP, TCP, UDP, ICMP, Ether, sniff, raw
from confluent_kafka import Producer, Consumer, KafkaError, KafkaException
from confluent_kafka.admin import AdminClient
import threading
import time
from decimal import Decimal
from flask import Flask, jsonify, request
import threading
from sklearn.preprocessing import RobustScaler
import netifaces

APP_PATH = '/app/' 

KAFKA_BROKER = os.getenv('KAFKA_BROKER')
RAW_TOPIC = os.getenv('RAW_TOPIC')
PROCESSED_TOPIC = os.getenv('PROCESSED_TOPIC')
PREDICTIONS_TOPIC = os.getenv('PREDICTIONS_TOPIC')

FLASK_PORT = int(os.getenv('FLASK_PORT', 5001))
CAPTURE_INTERFACE = os.getenv('CAPTURE_INTERFACE', 'eth0')

TORCHSERVE_REQUESTS_URL = os.getenv('TORCHSERVE_REQUESTS', 'http://localhost:8080')
TORCHSERVE_MANAGEMENT_URL = os.getenv('TORCHSERVE_MANAGEMENT', 'http://localhost:8081')
TORCHSERVE_METRICS_URL = os.getenv('TORCHSERVE_METRICS', 'http://localhost:8082')
MODEL_NAME = os.getenv('MODEL_NAME', 'memory_autoencoder')
SCALER_PATH = '/app/scaler_data/robust_scaler.pkl'
ANOMALY_THRESHOLD = os.getenv('ANOMALY_THRESHOLD', 1)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s',
                    handlers=[logging.StreamHandler()])


logging.info(f"App directory contents: {os.listdir(APP_PATH)}")
logging.info(f"Configured Kafka broker URL: {KAFKA_BROKER}")
logging.info(f"Configured TorchServe requests URL: {TORCHSERVE_REQUESTS_URL}")
logging.info(f"Configured TorchServe management URL: {TORCHSERVE_MANAGEMENT_URL}")
logging.info(f"Configured capture interface: {CAPTURE_INTERFACE}")

##################################################
# KAFKA MANAGERS AND UTILITY
##################################################

class ProducerManager:
    def __init__(self):
        self.producers = {}
        self.lock = threading.Lock()

    def get_producer(self, topic):
        with self.lock:
            if topic not in self.producers:
                config = producer_config.copy()
                config['client.id'] = f'producer-{topic}'
                try:
                    self.producers[topic] = Producer(config)
                    logging.info(f"Successfully created producer for topic: {topic}")
                except KafkaException as e:
                    logging.error(f"Failed to create producer for topic {topic}: {e}")
                    raise
            return self.producers[topic]

class ConsumerManager:
    def __init__(self):
        self.consumers = {}
        self.lock = threading.Lock()

    def get_consumer(self, topic, group_id):
        with self.lock:
            key = f"{topic}-{group_id}"
            if key not in self.consumers:
                config = consumer_config.copy()
                config['group.id'] = group_id
                try:
                    self.consumers[key] = Consumer(config)
                    self.consumers[key].subscribe([topic])
                    logging.info(f"Successfully created consumer for topic: {topic}, group: {group_id}")
                except KafkaException as e:
                    logging.error(f"Failed to create consumer for topic {topic}, group {group_id}: {e}")
                    raise
            return self.consumers[key]

producer_manager = ProducerManager()
consumer_manager = ConsumerManager()

producer_config = {
    'bootstrap.servers': KAFKA_BROKER,
    'client.dns.lookup': 'use_all_dns_ips',
    'broker.address.family': 'v4'
}

consumer_config = {
    'bootstrap.servers': KAFKA_BROKER,
    'auto.offset.reset': 'earliest',
    'client.dns.lookup': 'use_all_dns_ips',
    'broker.address.family': 'v4'
}

# Uses AdminClient to verify the existence of a topic
def topic_exists(broker_address, topic_name):
    try:
        admin_client = AdminClient({'bootstrap.servers': broker_address})
        metadata = admin_client.list_topics(timeout=10)
        return topic_name in metadata.topics
    except Exception as e:
        logging.error(f"Error checking topic existence: {e}")
        return False


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

def get_available_interfaces():
    logging.info('Getting available interfaces')
    return netifaces.interfaces()

def check_interface(interface):
    available_interfaces = get_available_interfaces()
    logging.info(f"Checking if {interface} is available.")
    if interface not in available_interfaces:
        logging.error(f"{interface} not found. Available interfaces: {available_interfaces}")
        return False
    logging.info(f"{interface} is available.")
    return True

def check_ports(interface, ports, retry_interval=5):
    # This function checks if the specified ports are being forwarded to the interface
    logging.info(f"Checking if ports {ports} are being forwarded to interface {interface}")

    def packet_callback(packet):
        if IP in packet:
            if packet[IP].dst == interface:
                if TCP in packet and packet[TCP].dport in ports:
                    logging.info(f"Traffic detected on port {packet[TCP].dport}")
                    return True
                if UDP in packet and packet[UDP].dport in ports:
                    logging.info(f"Traffic detected on port {packet[UDP].dport}")
                    return True
        return False

    while True:
        try:
            # Capture packets for a short duration to check for incoming traffic
            result = sniff(iface=interface, prn=packet_callback, timeout=10)
            if result:
                logging.info(f"Traffic detected on ports {ports}")
                return True
        except Exception as e:
            logging.error(f"Error checking ports: {e}")
        logging.info(f"No traffic detected on ports {ports} for interface {interface}. Retrying in {retry_interval} seconds...")
        time.sleep(retry_interval)

def capture_live_traffic(interface, broker_address):
    if not check_ports(interface, [80, 443]):
        logging.error(f"Ports are not being forwarded to interface {interface}")
        return
    try:
        sniff(iface=interface, prn=lambda x: produce_raw_data(x, broker_address), store=0)
    except Exception as e:
        logging.error(f"Thread 1: Error capturing live traffic: {e}")

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

    try:
        producer = producer_manager.get_producer(RAW_TOPIC)
    except KafkaException as e:
        logging.error(f"Thread 1: Failed to get producer for {RAW_TOPIC}: {e}")
        return
    retry_count = 0
    max_retries = 5

    while retry_count < max_retries:
        try:
            for packet in packets:
                serialized_packet = serialize_packet(packet)
                if serialized_packet:
                    serialized_packet = {
                        "time": packet.time,
                        "data": raw(packet).hex()
                    }
                    producer.produce(
                        topic=RAW_TOPIC,
                        key=str(packet.time),
                        value=json.dumps(serialized_packet, cls=CustomEncoder)
                    )
            producer.flush()
            logging.debug("Thread 1: Finished producing raw data to Kafka")
            break
        except:
            logging.error(f"Thread 1: Failed to produce raw data")
            retry_count += 1
            time.sleep(2 ** retry_count)  # Exponential backoff

    if retry_count == max_retries:
        logging.error("Thread 1: Max retries reached. Failed to produce raw data.")


##################################################
# PACKET PROCESSING - thread 2
##################################################

class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super(CustomEncoder, self).default(obj)


def fit_scaler(broker_address, sample_size=1000):    
    consumer = consumer_manager.get_consumer(RAW_TOPIC, 'scaler_fitting')
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
    logging.info('Thread 2: Starting raw data processing')
    consumer = consumer_manager.get_consumer(RAW_TOPIC, 'network_data')

    try:
        producer = producer_manager.get_producer(PROCESSED_TOPIC)
    except KafkaException as e:
        logging.error(f"Thread 2: Failed to get producer for {PROCESSED_TOPIC}: {e}")
        return
    
    # Fit the scaler on initial sample of data
    initial_sample_size = 100
    scaler, current_data = fit_scaler(broker_address, sample_size=initial_sample_size)
    logging.info("Thread 2: Scaler fitted on initial sample data")

    messages_processed = 0
    refit_threshold = initial_sample_size * 10
    new_data = []

    try:
        logging.info(f"Thread 2: Producing to {PROCESSED_TOPIC}")
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
                value = json.loads(msg.value().decode('utf-8'))

                # Ensure the consumed message has the expected format
                if 'time' in value and 'data' in value:
                    key = str(value['time'])
                    features = process_packet(value)

                logging.debug("Thread 2: Consumed message with key: {}, value: {}".format(key, value))
                
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
                        processed_value = {
                            "timestamp": float(msg.timestamp()[1]),
                            "features": scaled_features[0].tolist()
                        }
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

training_status = {
    "status": "idle",
    "progress": 0,
    "message": ""
}

def update_training_status(status, progress, message):
    global training_status
    training_status = {
        "status": status,
        "progress": progress,
        "message": message
    }

# Modify train_model_process function
def train_model_process(start_date, end_date):
    update_training_status("starting", 0, "Initiating model training process")

    try:
        update_training_status("checking_torchserve", 10, "Checking TorchServe availability")
        if not check_torchserve_availability():
            update_training_status("error", 0, "TorchServe is not available")
            return

        update_training_status("fetching_data", 20, "Fetching data from Kafka")
        data = fetch_data_from_kafka(start_date, end_date)
        update_training_status("data_fetched", 40, f"Fetched {len(data)} records from Kafka")

        update_training_status("training", 50, "Training model")
        if train_and_set_inference_mode(data):
            update_training_status("completed", 100, "Model training completed and set to inference mode")
            threading.Thread(target=prediction_thread).start()
        else:
            update_training_status("error", 0, "Failed to train model and set to inference mode")
    except Exception as e:
        logging.error(f"Error during model training process: {str(e)}")
        update_training_status("error", 0, f"Error during training: {str(e)}")

def fetch_data_from_kafka(start_date, end_date):
    logging.info(f"Fetching data from Kafka between {start_date} and {end_date}")
    consumer = consumer_manager.get_consumer(PROCESSED_TOPIC, 'model_training_group')

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
        pass

    return data

def check_torchserve_availability():
    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = requests.get(f"{TORCHSERVE_MANAGEMENT_URL}/models")
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
        response = requests.delete(f"{TORCHSERVE_MANAGEMENT_URL}/models/{model_name}/{version}")
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
            url = f"{TORCHSERVE_MANAGEMENT_URL}/models/{MODEL_NAME}"
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
    model_version = "1.0"
    max_retries = 5

    logging.info("Attempting to train model.")
    
    # Check if the model is already registered
    try:
        response = requests.get(f"{TORCHSERVE_MANAGEMENT_URL}/models/{MODEL_NAME}")
        if response.status_code == 200:
            logging.info(f"Model {MODEL_NAME} is registered.")
    except requests.RequestException as e:
        logging.error(f"Error checking existing model registration: {e}")
    features_list = [packet['features'] for packet in data]
    
    for attempt in range(max_retries):
        try:
            url = f"{TORCHSERVE_REQUESTS_URL}/predictions/{MODEL_NAME}"
            headers = {'X-Request-Type': 'train'}
            
            logging.info(f"Sending model training request with {features_list} (attempt {attempt + 1})")
            response = requests.post(url, json=features_list, headers=headers)
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
    url = f"{TORCHSERVE_REQUESTS_URL}/predictions/{MODEL_NAME}"
    
    logging.info(f"Querying model with data: {data}")
    try:
        response = requests.post(url, json=data)
        response.raise_for_status()
        prediction = response.json()
        logging.debug(f"Received prediction {prediction} using weights from: {prediction.get('weights_file')}")
        return prediction
    except requests.exceptions.RequestException as e:
        logging.error(f"Error querying model: {e}")
        return {"error": "Failed to get prediction"}

def prediction_thread():
    logging.info("Starting prediction thread")
    consumer = consumer_manager.get_consumer(PROCESSED_TOPIC, 'prediction_group')

    # Wait for the model to be ready before starting predictions
    wait_for_model_ready()
    
    try:
        producer = producer_manager.get_producer(PREDICTIONS_TOPIC)
    except KafkaException as e:
        logging.error(f"Failed to get producer for {PREDICTIONS_TOPIC}: {e}")
        return

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
                # Ensure the consumed message has the expected format
                if 'timestamp' in value and 'features' in value:
                    prediction = query_model(value['features'])
                
                if "error" in prediction:
                    logging.error(f"Error in prediction: {prediction['error']}")
                    continue
                
                # Extract anomaly results and produce individual messages
                anomaly_results = prediction.get('anomaly_results', [])
                #weights_file = prediction.get('weights_file')

                for result in anomaly_results:
                    packet_id = result['packet_id']
                    if float(result['reconstruction_error']) < float(ANOMALY_THRESHOLD):
                        anomaly = False
                    else:
                        anomaly = True
                    output = {
                        "packet_id": str(packet_id),
                        "reconstruction_error": float(result['reconstruction_error']),
                        "is_anomaly": bool(anomaly)
                    }
                    producer.produce(PREDICTIONS_TOPIC, key=str(packet_id), value=json.dumps(output))                
                producer.flush()
                logging.info(f"Produced predictions for {len(anomaly_results)} packets")
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding message: {e}")
            except Exception as e:
                logging.error(f"Unexpected error processing message: {e}")

    except KeyboardInterrupt:
        logging.info("Interrupted.")
    finally:
        pass

##################################################
# FLASK FOR MODEL TRAINING - thread 4
##################################################

model_training = False
app = Flask(__name__)

@app.route('/train', methods=['POST'])
def start_training():
    data = request.json
    start_date = data.get('startDate')
    end_date = data.get('endDate')

    if not start_date or not end_date:
        return jsonify({"message": "Start and end dates are required"}), 400

    threading.Thread(target=train_model_process, args=(start_date, end_date)).start()
    return jsonify({"message": "Model training initiated"}), 202

@app.route('/status', methods=['GET'])
def get_status():
    return jsonify(training_status)

@app.route('/health', methods=['GET'])
def health_check():
    # Implement health check logic
    return jsonify({"status": "healthy"}), 200


##################################################
# MAIN
##################################################

def main():
    logging.info("MLSEC data processing engine starting")
    time.sleep(5)

    available_interfaces = get_available_interfaces()
    logging.info(f"Available network interfaces: {available_interfaces}")

    if CAPTURE_INTERFACE not in available_interfaces:
        logging.error(f"Specified interface {CAPTURE_INTERFACE} not found. Please choose from: {available_interfaces}")
        return

    threads = [
        threading.Thread(target=capture_live_traffic, args=(CAPTURE_INTERFACE, KAFKA_BROKER)),
        threading.Thread(target=process_raw_data, args=(KAFKA_BROKER,)),
        threading.Thread(target=prediction_thread),
        threading.Thread(target=app.run, kwargs={'host': '0.0.0.0', 'port': FLASK_PORT})
    ]

    for thread in threads:
        thread.start()

    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        logging.info("Shutting down ...")

if __name__ == "__main__":
    main()