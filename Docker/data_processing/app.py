from datetime import datetime, timezone
import hashlib
import json
import os
import uuid
import pandas as pd
import requests
import logging
from decimal import Decimal
from scapy.fields import EDecimal
from scapy.all import IP, TCP, UDP, ICMP, Ether, sniff, raw, rdpcap, Raw
from confluent_kafka import Producer, Consumer, KafkaError, KafkaException
from confluent_kafka.admin import AdminClient
import threading
import time
from flask import Flask, jsonify, request
from sklearn.preprocessing import RobustScaler
import netifaces

APP_PATH = os.getenv('APP_PATH', '/app/')
KAFKA_BROKER = os.getenv('KAFKA_BROKER', 'kafka:9092')
RAW_TOPIC = os.getenv('RAW_TOPIC', 'raw_data')
PROCESSED_TOPIC = os.getenv('PROCESSED_TOPIC', 'processed_data')
PREDICTIONS_TOPIC = os.getenv('PREDICTIONS_TOPIC', 'predictions')
TRAINING_TOPIC = os.getenv('TRAINING_TOPIC', 'training_data')
FLASK_PORT = int(os.getenv('FLASK_PORT', 5001))
CAPTURE_INTERFACE = os.getenv('CAPTURE_INTERFACE', 'eth0')
TORCHSERVE_REQUESTS_URL = os.getenv('TORCHSERVE_REQUESTS', 'http://localhost:8080')
TORCHSERVE_MANAGEMENT_URL = os.getenv('TORCHSERVE_MANAGEMENT', 'http://localhost:8081')
TORCHSERVE_METRICS_URL = os.getenv('TORCHSERVE_METRICS', 'http://localhost:8082')
MODEL_NAME = os.getenv('MODEL_NAME', 'memory_autoencoder')
SCALER_PATH = os.getenv('SCALER_PATH', '/app/scaler_data/robust_scaler.pkl')
TRAINING_DATA_PATH = os.getenv('TRAINING_DATA_PATH', '/app/training_data')
ANOMALY_THRESHOLD = os.getenv('ANOMALY_THRESHOLD', 1)

protocol_map = {1: "ICMP", 6: "TCP", 17: "UDP", 2: "IGMP"} 
is_training_period = False
training_end_time = None
thread_local = threading.local()

def get_thread_name():
    if not hasattr(thread_local, 'thread_name'):
        thread_local.thread_name = threading.current_thread().name
    return thread_local.thread_name

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
    def __init__(self, config):
        self.consumers = {}
        self.lock = threading.Lock()
        self.consumer_config = config  # Store the base config

    def get_consumer(self, topic, group_id, config=None):
        with self.lock:
            key = f"{topic}-{group_id}"
            if key not in self.consumers:
                config = config or self.consumer_config.copy()
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

consumer_manager = ConsumerManager(consumer_config)

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

class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (Decimal, EDecimal)):
            return float(obj)
        elif isinstance(obj, bytes):
            return obj.decode('utf-8', errors='replace')
        elif hasattr(obj, '__str__'):
            return str(obj)
        return super(CustomEncoder, self).default(obj)

def get_available_interfaces():
    logging.info('Getting available interfaces')
    return netifaces.interfaces()

def start_training_period(end_time):
    global is_training_period, training_end_time
    is_training_period = True
    training_end_time = end_time
    logging.info(f"Training period started, will end at {end_time}")
    
    # Schedule the end of the training period
    delay = (end_time - datetime.now(timezone.utc)).total_seconds()
    threading.Timer(delay, end_training_period).start()

def end_training_period():
    global is_training_period, training_end_time
    is_training_period = False
    training_end_time = None
    logging.info("Training period ended")

def process_time_window(start_date, end_date):
    global is_training_period, training_end_time
    
    # Convert start_date and end_date to datetime objects if they're not already
    if isinstance(start_date, (int, float)):
        start_date = datetime.fromtimestamp(start_date / 1000, timezone.utc)
    if isinstance(end_date, (int, float)):
        end_date = datetime.fromtimestamp(end_date / 1000, timezone.utc)
    
    current_time = datetime.now(timezone.utc)
    
    if start_date > current_time:
        # Schedule the start of the training period
        delay = (start_date - current_time).total_seconds()
        threading.Timer(delay, start_training_period, args=[end_date]).start()
        logging.info(f"Training period scheduled to start at {start_date}")
    else:
        # Start the training period immediately
        start_training_period(end_date)

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

def capture_live_traffic(interface):
    if not check_ports(interface, [80, 443]):
        logging.error(f"[TrafficCaptureThread] Ports are not being forwarded to interface {interface}")
        return
    try:
        def packet_callback(packet):
            logging.debug(f"[TrafficCaptureThread] Captured packet type: {type(packet)}")
            logging.debug(f"[TrafficCaptureThread] Packet summary: {packet.summary()}")
            produce_raw_data([packet])

        sniff(iface=interface, prn=packet_callback, store=0)
    except Exception as e:
        logging.error(f"[TrafficCaptureThread] Error capturing live traffic: {e}")

def generate_unique_id():
    return str(uuid.uuid4())

def read_pcap(file_path, broker_address):
    logging.info(f"Processing uploaded PCAP file: {file_path}")
    try:
        packets = rdpcap(file_path)
        logging.info(f"Successfully unpacked {len(packets)} packets from training file")
        produce_raw_data([(packet, True) for packet in packets])  # Mark all packets as training data
    except Exception as e:
        logging.error(f"Error processing uploaded PCAP: {e}")

##################################################
# PACKET PROCESSING
##################################################

def serialize_packet(packet, is_training=False):
    thread_name = get_thread_name()
    logging.debug(f"[{thread_name}] Serializing packet of type: {type(packet)}")

    if isinstance(packet, tuple) and len(packet) == 2:
        packet, is_training = packet

    try:
        features, human_readable = process_packet(packet)
        if features is None or human_readable is None:
            logging.warning(f"[{thread_name}] Unable to process packet, skipping serialization")
            return None
        
        serialized = {
            "id": generate_unique_id(),
            "time": float(packet.time),
            "data": raw(packet).hex(),
            "is_training": is_training,
            "human_readable": human_readable
        }
        logging.debug(f"[{thread_name}] Serialized packet: {serialized}")
        return serialized
    except Exception as e:
        logging.error(f"[{thread_name}] Error serializing packet: {e}")
        return None

def produce_raw_data(packets):
    thread_name = get_thread_name()
    logging.debug(f"[{thread_name}] Producing {len(packets)} to {RAW_TOPIC}")
    producer = producer_manager.get_producer(RAW_TOPIC)
    
    for packet in packets:
        logging.debug(f"[{thread_name}] Processing packet type: {type(packet)}")
        
        current_time = datetime.now(timezone.utc)
        is_training = is_training_period and (training_end_time is None or current_time <= training_end_time)
        
        serialized_packet = serialize_packet(packet, is_training)
        if serialized_packet:
            try:
                producer.produce(
                    RAW_TOPIC,
                    key=serialized_packet['id'],
                    value=json.dumps(serialized_packet, cls=CustomEncoder)
                )
                producer.poll(0)
            except Exception as e:
                logging.error(f"[{thread_name}] Error producing raw data: {e}")
        else:
            logging.warning(f"[{thread_name}] Skipping packet due to serialization failure")
    
    logging.debug(f"[{thread_name}] Produced {len(packets)} to {RAW_TOPIC}: {packets}")
    producer.flush()

def fit_scaler(sample_size=1000):
    consumer = consumer_manager.get_consumer(RAW_TOPIC, 'scaler_fitting')
    features_list = []
    while len(features_list) < sample_size:
        msg = consumer.poll(1.0)
        if msg is None:
            continue
        if msg.error():
            continue
        
        value = json.loads(msg.value())
        features, _ = process_packet(value)
        if features:
            features_list.append(features)
    
    if features_list and all(len(features) == 6 for features in features_list):
        df = pd.DataFrame(features_list, columns=['src_ip', 'src_port', 'dst_ip', 'dst_port', 'protocol', 'flags'])
        scaler = RobustScaler()
        scaler.fit(df)
        return scaler, df
    else:
        raise ValueError("Insufficient or incorrect feature data to create DataFrame")

def update_scaler(scaler, new_data, current_data):
    combined_data = pd.concat([current_data, new_data], ignore_index=True)
    updated_scaler = RobustScaler()
    updated_scaler.fit(combined_data)
    return updated_scaler, combined_data

def process_raw_data():
    logging.info('Thread 2: Starting raw data processing')
    consumer = consumer_manager.get_consumer(RAW_TOPIC, 'network_data')

    try:
        processed_producer = producer_manager.get_producer(PROCESSED_TOPIC)
        training_producer = producer_manager.get_producer(TRAINING_TOPIC)
    except KafkaException as e:
        logging.error(f"Thread 2: Failed to get producers: {e}")
        return
    
    # Fit the scaler on initial sample of data
    initial_sample_size = 100
    scaler, current_data = fit_scaler(sample_size=initial_sample_size)
    logging.info("Thread 2: Scaler fitted on initial sample data")

    messages_processed = 0
    refit_threshold = initial_sample_size * 10
    new_data = []

    try:
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

                if 'id' in value and 'time' in value and 'data' in value and 'is_training' in value and 'human_readable' in value:
                    unique_id = value['id']
                    is_training = value['is_training']
                    features, _ = process_packet(value)

                    if features:
                        try:
                            logging.debug("Thread 2: Extracted features: {}".format(features))
                            # Ensure the DataFrame only includes the relevant columns
                            ordered_columns = ['src_ip', 'src_port', 'dst_ip', 'dst_port', 'protocol', 'flags']
                            df = pd.DataFrame([features], columns=ordered_columns)
                            
                            # Transform the features using the current scaler
                            scaled_features = scaler.transform(df)
                            processed_value = {
                                "id": unique_id,
                                "timestamp": float(msg.timestamp()[1]),
                                "features": scaled_features[0].tolist(),
                                "is_training": is_training,
                                "human_readable": value['human_readable']
                            }
                            processed_producer.produce(
                                topic=PROCESSED_TOPIC,
                                key=unique_id,
                                value=json.dumps(processed_value, cls=CustomEncoder),
                            )
                            processed_producer.flush()

                            if is_training:
                                training_producer.produce(
                                    topic=TRAINING_TOPIC,
                                    key=unique_id,
                                    value=json.dumps(processed_value, cls=CustomEncoder),
                                )
                                training_producer.flush()

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
                else:
                    logging.error("Thread 2: Received message with unexpected format")
    except Exception as e:
        logging.error("Thread 2: Failed to consume or produce messages: {}".format(e))
    finally:
        processed_producer.flush()
        training_producer.flush()

def scale_features(features, scaler):
    try:
        scaled = scaler.transform([features])[0]
        logging.debug(f"Thread 2: Scaled features: {scaled}")
        return scaled
    except Exception as e:
        logging.error(f"Thread 2: Error scaling features: {e}")
        return None

def ip_to_hash(ip: str) -> int:
    return int(hashlib.sha256(ip.encode()).hexdigest()[:8], 16)

def flags_to_int(flags: str) -> int:
    return sum((0x01 << i) for i, f in enumerate('FSRPAUEC') if f in flags)

def protocol_to_int(proto: int) -> int:
    protocol_map = {1: 1, 6: 2, 17: 3, 2: 4}  # ICMP, TCP, UDP, IGMP
    return protocol_map.get(proto, 0)

def process_packet(packet):
    thread_name = get_thread_name()
    logging.debug(f"[{thread_name}] Processing packet of type: {type(packet)}")
    try:
        if isinstance(packet, dict):
            packet_data = bytes.fromhex(packet['data'])  # Convert hex string back to bytes
            packet = Ether(packet_data)  # Convert bytes to Ether packet
        if isinstance(packet, Raw):
            packet = Ether(bytes(packet))  # Convert Raw packet to Ether packet
        if isinstance(packet, Ether):
            logging.debug(f"[{thread_name}] Processing Ether packet: {packet.summary()}")
            return extract_features(packet)
        else:
            logging.warning(f"[{thread_name}] Unexpected input type: {type(packet)}")
            return None, None
    except Exception as e:
        logging.error(f"[{thread_name}] Error processing packet: {e}")
        return None, None

def extract_features(packet):
    if IP in packet:
        ip_layer = packet[IP]
        tcp_layer = packet[TCP] if TCP in packet else None

        features = {
            'src_ip': ip_to_hash(ip_layer.src),
            'dst_ip': ip_to_hash(ip_layer.dst),
            'protocol': protocol_to_int(ip_layer.proto),
            'flags': flags_to_int(tcp_layer.flags) if tcp_layer and hasattr(tcp_layer, 'flags') else 0,
            'src_port': tcp_layer.sport if tcp_layer else 0,
            'dst_port': tcp_layer.dport if tcp_layer else 0
        }
        human_readable_protocol = protocol_map.get(ip_layer.proto, "Unknown")
        human_readable = {
            'src_ip': ip_layer.src,
            'dst_ip': ip_layer.dst,
            'protocol': human_readable_protocol,
            'flags': tcp_layer.flags if tcp_layer and hasattr(tcp_layer, 'flags') else "",
            'src_port': tcp_layer.sport if tcp_layer else 0,
            'dst_port': tcp_layer.dport if tcp_layer else 0
        }

        return features, human_readable
    else:
        logging.debug(f"[{get_thread_name()}] Non-IP packet received, cannot process")
        return None, None

##################################################
# MODEL - thread 3
##################################################

model_ready_event = threading.Event()
training_status = {
    "status": "idle",
    "progress": 0,
    "message": ""
}

def train_model_process():
    update_training_status("starting", 0, "Initiating model training process")

    try:
        update_training_status("checking_torchserve", 10, "Checking TorchServe availability")
        if not check_torchserve_availability():
            update_training_status("error", 0, "TorchServe is not available")
            return

        update_training_status("fetching_data", 20, "Fetching data from Kafka")
        data = fetch_training_data()
        update_training_status("data_fetched", 40, f"Fetched {len(data)} records from Kafka")
        time.sleep(5)
        update_training_status("training", 50, "Training model")
        if train_and_set_inference_mode(data):
            update_training_status("completed", 100, "Model training completed and set to inference mode")
            threading.Thread(target=prediction_thread).start()
        else:
            update_training_status("error", 0, "Failed to train model and set to inference mode")
    except Exception as e:
        logging.error(f"Error during model training process: {str(e)}")
        update_training_status("error", 0, f"Error during training: {str(e)}")

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

def fetch_training_data():
    logging.info("Fetching all unread data from training topic")
    consumer_config = consumer_manager.consumer_config.copy()
    consumer_config['auto.offset.reset'] = 'earliest'
    consumer = consumer_manager.get_consumer(TRAINING_TOPIC, 'training_group', config=consumer_config)

    data = []
    max_empty_polls = 5
    empty_poll_count = 0

    try:
        while empty_poll_count < max_empty_polls:
            msg = consumer.poll(1.0)  # Increased timeout to 1 second
            if msg is None:
                empty_poll_count += 1
                logging.info(f"Empty poll {empty_poll_count}/{max_empty_polls}")
                continue
            if msg.error():
                logging.error(f"Consumer error: {msg.error()}")
                continue

            try:
                value = json.loads(msg.value().decode('utf-8'))
                data.append(value)
                empty_poll_count = 0  # Reset empty poll count on successful message
                logging.debug(f"Received message: {value}")
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding message: {e}")
            except Exception as e:
                logging.error(f"Unexpected error processing message: {e}")

    finally:
        consumer.close()
    
    logging.info(f"Fetched data of size {len(data)} from {TRAINING_TOPIC}")
    return data

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

    # Extract features from the data
    features_list = [packet['features'] for packet in data]
    
    for attempt in range(max_retries):
        try:
            url = f"{TORCHSERVE_REQUESTS_URL}/predictions/{MODEL_NAME}"
            headers = {'X-Request-Type': 'train'}
            
            logging.info(f"Sending model training request with {len(features_list)} feature sets (attempt {attempt + 1})")
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

def update_training_status(status, progress, message):
    global training_status
    training_status = {
        "status": status,
        "progress": progress,
        "message": message
    }

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
                if 'id' in value and 'features' in value:
                    prediction = query_model(value['features'])
                
                    if "error" in prediction:
                        logging.error(f"Error in prediction: {prediction['error']}")
                        continue
                    
                    # Extract anomaly results and produce individual messages
                    anomaly_results = prediction.get('anomaly_results', [])

                    for result in anomaly_results:
                        packet_id = value['id']  # Use the unique ID from the input
                        reconstruction_error = float(result['reconstruction_error'])
                        is_anomaly = reconstruction_error >= float(ANOMALY_THRESHOLD)
                        output = {
                            "packet_id": packet_id,
                            "reconstruction_error": reconstruction_error,
                            "is_anomaly": is_anomaly
                        }
                        producer.produce(PREDICTIONS_TOPIC, key=packet_id, value=json.dumps(output))                
                    producer.flush()
                    logging.debug(f"Produced prediction for packet {packet_id}")
                else:
                    logging.warning(f"Received message with unexpected format: {value}")
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding message: {e}")
            except Exception as e:
                logging.error(f"Unexpected error processing message: {e}")

    except KeyboardInterrupt:
        logging.info("Interrupted.")
    finally:
        consumer.close()

def wait_for_model_ready():
    logging.info("Waiting for model to be ready...")
    model_ready_event.wait()  # Block until the event is set
    logging.info("Model is ready")

def query_model(data):
    url = f"{TORCHSERVE_REQUESTS_URL}/predictions/{MODEL_NAME}"
    
    logging.debug(f"Querying model with data: {data}")
    try:
        response = requests.post(url, json=data)
        response.raise_for_status()
        prediction = response.json()
        logging.debug(f"Received prediction {prediction} using weights from: {prediction.get('weights_file')}")
        return prediction
    except requests.exceptions.RequestException as e:
        logging.error(f"Error querying model: {e}")
        return {"error": "Failed to get prediction"}


##################################################
# FLASK FOR MODEL TRAINING - thread 4
##################################################

model_training = False
app = Flask(__name__)

@app.route('/train_data', methods=['POST'])
def train_data():
    data = request.json
    if 'file_path' in data:
        # Process PCAP file
        file_path = os.path.join(TRAINING_DATA_PATH, data['file_path'])  # Use TRAINING_DATA_PATH
        read_pcap(file_path, KAFKA_BROKER)
    elif 'startDate' in data and 'endDate' in data:
        # Process data within time window
        start_date = datetime.fromtimestamp(int(data['startDate']) / 1000, timezone.utc)
        end_date = datetime.fromtimestamp(int(data['endDate']) / 1000, timezone.utc)
        process_time_window(start_date, end_date)
    else:
        return jsonify({"error": "Invalid training data provided"}), 400

    return jsonify({"message": "Training data uploaded"}), 202

@app.route('/training_start', methods=['POST'])
def start_training_job():
    threading.Thread(target=train_model_process).start()
    return jsonify({"message": "Training job started"}), 200

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
    logging.info(CAPTURE_INTERFACE)
    threads = [
        threading.Thread(name='TrafficCaptureThread', target=capture_live_traffic, args=(CAPTURE_INTERFACE,)),
        threading.Thread(name='DataProcessingThread', target=process_raw_data),
        threading.Thread(name='PredictionThread', target=prediction_thread),
        threading.Thread(name='AppThread', target=app.run, kwargs={'host': '0.0.0.0', 'port': FLASK_PORT})
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