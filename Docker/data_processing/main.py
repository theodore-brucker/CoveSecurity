from datetime import datetime, timezone
import json
import os
import uuid
import requests
import logging
from decimal import Decimal
from scapy.fields import EDecimal
from scapy.all import IP, TCP, UDP, ICMP, sniff, rdpcap
from confluent_kafka import KafkaException
import threading
import time
from flask import Flask, jsonify, request
import numpy as np
from sklearn.preprocessing import RobustScaler
import ipaddress
from kafka_utils import initialize_kafka_managers

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
MODEL_NAME = os.getenv('MODEL_NAME', 'transformer_autoencoder')
TRAINING_DATA_PATH = os.getenv('TRAINING_DATA_PATH', '/app/training_data')
ANOMALY_THRESHOLD = float(os.getenv('ANOMALY_THRESHOLD', 1))
SEQUENCE_LENGTH = int(os.getenv('SEQUENCE_LENGTH', 16))
FEATURE_COUNT = int(os.getenv('FEATURE_COUNT', 12))

protocol_map = {1: "ICMP", 6: "TCP", 17: "UDP", 2: "IGMP"} 
is_training_period = False
training_end_time = None
thread_local = threading.local()
producer_manager, consumer_manager = initialize_kafka_managers(KAFKA_BROKER)

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
# TRAFFIC CAPTURE - thread 1
##################################################

class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (Decimal, EDecimal)):
            return float(obj)
        elif isinstance(obj, bytes):
            return obj.decode('utf-8', errors='replace')
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, (np.int_, np.intc, np.intp, np.int8, np.int16, np.int32, np.int64, np.uint8, np.uint16, np.uint32, np.uint64)):
            return int(obj)
        elif isinstance(obj, (np.float_, np.float16, np.float32, np.float64)):
            return float(obj)
        elif hasattr(obj, '__str__'):
            return str(obj)
        logging.warning(f"CustomEncoder: Unhandled type {type(obj)}")
        return super(CustomEncoder, self).default(obj)

class GlobalScaler:
    def __init__(self, feature_count):
        self.scaler = RobustScaler()
        self.feature_count = feature_count
        self.is_fitted = False

    def fit(self, data):
        self.scaler.fit(data)
        self.is_fitted = True

    def partial_fit(self, data):
        if not self.is_fitted:
            self.fit(data)
        else:
            # RobustScaler doesn't have partial_fit, so we'll just refit
            self.scaler.fit(data)

    def transform(self, data):
        if not self.is_fitted:
            self.fit(data)
        return self.scaler.transform(data)

class PacketSequenceBuffer:
    def __init__(self, sequence_length=SEQUENCE_LENGTH, feature_dim=FEATURE_COUNT):
        self.sequence_length = sequence_length
        self.feature_dim = feature_dim
        self.feature_buffer = np.zeros((sequence_length, feature_dim), dtype=np.float32)
        self.human_readable_buffer = []
        self.timestamp_buffer = []
        self.global_scaler = GlobalScaler(feature_dim)
        self.current_index = 0

    def add_packet(self, packet_features, human_readable, timestamp):
        if len(packet_features) != self.feature_dim:
            logging.warning(f"Packet features length mismatch. Expected {self.feature_dim}, got {len(packet_features)}")
            return None, None

        self.feature_buffer[self.current_index] = packet_features
        self.human_readable_buffer.append(human_readable)
        self.timestamp_buffer.append(timestamp)
        self.current_index += 1

        if self.current_index == self.sequence_length:
            # Calculate average inter-packet time
            inter_packet_times = np.diff(self.timestamp_buffer)
            avg_inter_packet_time = np.mean(inter_packet_times)
            
            # Normalize the average inter-packet time (example normalization, adjust as needed)
            normalized_avg_time = (np.log1p(avg_inter_packet_time) / np.log1p(1)) * 2 - 1  # Assuming 1 second as max
            
            # Replace the 4th feature (index 3) with the new inter-packet time feature
            self.feature_buffer[:, 3] = normalized_avg_time

            scaled_sequence = self.global_scaler.transform(self.feature_buffer)
            
            # Add debugging information here
            logging.info("Scaled sequence statistics:")
            for i in range(self.feature_dim):
                feature_column = scaled_sequence[:, i]
                logging.info(f"Feature {i}: min={feature_column.min():.4f}, max={feature_column.max():.4f}, "
                              f"mean={feature_column.mean():.4f}, std={feature_column.std():.4f}")
            
            # Additional overall statistics
            logging.info(f"Overall: min={scaled_sequence.min():.4f}, max={scaled_sequence.max():.4f}, "
                          f"mean={scaled_sequence.mean():.4f}, std={scaled_sequence.std():.4f}")

            human_readable_sequence = self.human_readable_buffer.copy()
            for hr in human_readable_sequence:
                hr['avg_inter_packet_time'] = avg_inter_packet_time

            self.current_index = 0
            self.human_readable_buffer.clear()
            self.timestamp_buffer.clear()
            return scaled_sequence.tolist(), human_readable_sequence
        return None, None

def safe_convert(value):
    if isinstance(value, (int, float)):
        return value
    try:
        return int(value)
    except:
        try:
            return float(value)
        except:
            return 0

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
        packet_buffer = PacketSequenceBuffer()
        reset_counter = 0
        def packet_callback(packet):
            nonlocal packet_buffer, reset_counter
            try:
                logging.debug(f"[TrafficCaptureThread] Captured packet type: {type(packet)}")
                logging.debug(f"[TrafficCaptureThread] Packet summary: {packet.summary()}")
                features, human_readable, timestamp = process_packet(packet)
                if features is not None and len(features) == FEATURE_COUNT:
                    feature_sequence, human_readable_sequence = packet_buffer.add_packet(features, human_readable, timestamp)
                    if feature_sequence is not None:
                        is_training = is_training_period and datetime.now(timezone.utc) <= training_end_time
                        produce_raw_data([feature_sequence], [human_readable_sequence], is_training)
                else:
                    logging.warning(f"[TrafficCaptureThread] Invalid features: {features}")
                reset_counter += 1
                if reset_counter >= 10000:  # Reset every 10000 packets
                    packet_buffer = PacketSequenceBuffer()
                    reset_counter = 0
                    logging.info("[TrafficCaptureThread] PacketSequenceBuffer reset")
            except Exception as e:
                logging.error(f"[TrafficCaptureThread] Error processing individual packet: {str(e)}", exc_info=True)

        logging.info(f"[TrafficCaptureThread] Starting packet capture on interface: {interface}")
        sniff(iface=interface, prn=packet_callback, store=0)
    except Exception as e:
        logging.error(f"[TrafficCaptureThread] Error capturing live traffic: {str(e)}", exc_info=True)

def produce_raw_data(feature_sequences, human_readable_sequences, is_training=False):
    producer = producer_manager.get_producer(RAW_TOPIC)
    logging.debug(f'Attempting to produce {len(feature_sequences)} raw sequences')
    valid_sequences = 0
    
    for feature_sequence, human_readable_sequence in zip(feature_sequences, human_readable_sequences):
        logging.debug(f"Processing sequence. Type: {type(feature_sequence)}, Length: {len(feature_sequence) if isinstance(feature_sequence, (list, np.ndarray)) else 'N/A'}")
        
        # Ensure feature_sequence is a list
        if not isinstance(feature_sequence, (list, np.ndarray)):
            logging.warning(f"feature_sequence is not a list or numpy array. Type: {type(feature_sequence)}. Skipping.")
            continue
        
        # Convert numpy array to list if necessary
        if isinstance(feature_sequence, np.ndarray):
            feature_sequence = feature_sequence.tolist()
        
        # Check sequence length
        if len(feature_sequence) != SEQUENCE_LENGTH:
            logging.warning(f"Skipping sequence of length {len(feature_sequence)}. Expected {SEQUENCE_LENGTH}")
            continue
        
        # Check packet structure
        if not all(isinstance(packet, (list, np.ndarray)) and len(packet) == FEATURE_COUNT for packet in feature_sequence):
            logging.warning(f"Invalid packet structure in sequence. Expected {SEQUENCE_LENGTH} packets, each with {FEATURE_COUNT} features.")
            continue
        
        # Check human_readable_sequence length
        if len(human_readable_sequence) != SEQUENCE_LENGTH:
            logging.warning(f"Human readable sequence length mismatch. Expected {SEQUENCE_LENGTH}, got {len(human_readable_sequence)}")
            continue
        
        try:
            serialized_sequence = {
                "id": generate_unique_id(),
                "timestamp": time.time(),
                "sequence": feature_sequence,
                "is_training": is_training,
                "human_readable": human_readable_sequence
            }
            
            json_data = json.dumps(serialized_sequence, cls=CustomEncoder)
            producer.produce(
                RAW_TOPIC,
                key=serialized_sequence['id'],
                value=json_data
            )
            producer.poll(0)
            valid_sequences += 1
            logging.debug(f"Produced sequence with ID {serialized_sequence['id']}")
        except Exception as e:
            logging.error(f"Error producing sequence: {e}", exc_info=True)
    
    try:
        producer.flush()
        logging.info(f'Finished producing {valid_sequences} out of {len(feature_sequences)} sequences.')
    except Exception as e:
        logging.error(f"Error flushing producer: {e}", exc_info=True)

def generate_unique_id():
    return str(uuid.uuid4())

def read_pcap(file_path, broker_address, is_training=True):
    update_training_status("Training data upload", 0, "Initiated file upload")
    logging.info(f"Processing uploaded PCAP file: {file_path}")
    try:
        update_training_status("Training data upload", 50, "Reading from file")
        packets = rdpcap(file_path)
        update_training_status("Training data upload", 75, "Successfully read from file")
        logging.info(f"Successfully unpacked {len(packets)} packets from training file")

        sequence_buffer = PacketSequenceBuffer()
        feature_sequences = []
        human_readable_sequences = []

        for packet in packets:
            features, human_readable, timestamp = process_packet(packet)
            if features is not None:
                feature_sequence, human_readable_sequence = sequence_buffer.add_packet(features, human_readable, timestamp)
                if feature_sequence is not None:
                    feature_sequences.append(feature_sequence)
                    human_readable_sequences.append(human_readable_sequence)

        if feature_sequences:
            produce_raw_data(feature_sequences, human_readable_sequences, is_training)

        update_training_status("Training data upload", 100, "Successfully uploaded training data")
    except Exception as e:
        logging.error(f"Error processing uploaded PCAP: {e}")

##################################################
# PACKET PROCESSING
##################################################

def process_raw_data():
    consumer = consumer_manager.get_consumer(RAW_TOPIC, 'network_data')
    processed_producer = producer_manager.get_producer(PROCESSED_TOPIC)
    training_producer = producer_manager.get_producer(TRAINING_TOPIC)
    
    while True:
        msg = consumer.poll(1.0)
        if msg is None:
            continue
        if msg.error():
            logging.error(f"Consumer error: {msg.error()}")
            continue

        try:
            value = json.loads(msg.value().decode('utf-8'))
            logging.debug(f"Received raw data message: {value}")

            if not all(k in value for k in ('id', 'sequence', 'is_training', 'human_readable')):
                logging.error(f"Missing keys in the message: {value}")
                continue

            sequence_id = value['id']
            is_training = value['is_training']
            sequence = value['sequence']
            human_readable_list = value['human_readable']

            if not isinstance(sequence, list) or len(sequence) != SEQUENCE_LENGTH:
                logging.error(f"Invalid sequence structure or length in message: {value}")
                continue

            if len(sequence) != len(human_readable_list):
                logging.error(f"Mismatch between sequence length and human_readable length in message: {value}")
                continue

            processed_value = {
                "id": sequence_id,
                "timestamp": value.get('timestamp', time.time()),
                "sequence": sequence,
                "is_training": is_training,
                "human_readable": human_readable_list
            }

            logging.debug(f"Producing processed data: {processed_value}")
            processed_producer.produce(
                topic=PROCESSED_TOPIC,
                key=sequence_id,
                value=json.dumps(processed_value, cls=CustomEncoder),
            )

            if is_training:
                training_producer.produce(
                    topic=TRAINING_TOPIC,
                    key=sequence_id,
                    value=json.dumps(processed_value, cls=CustomEncoder),
                )

            consumer.commit(msg)
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding message: {e}")
        except KeyError as ke:
            logging.error(f"KeyError: {ke} in message: {value}")
        except ValueError as ve:
            logging.error(f"ValueError: {ve}")
        except TypeError as te:
            logging.error(f"TypeError: {te}")
        except Exception as e:
            logging.error(f"Unexpected error processing data from raw topic: {e}")

def inspect_packet(packet):
    """Log detailed information about a packet for debugging."""
    logging.debug(f"Packet summary: {packet.summary()}")
    if IP in packet:
        ip = packet[IP]
        logging.debug(f"IP fields: {ip.fields}")
        logging.debug(f"IP flags type: {type(ip.flags)}, value: {ip.flags}")
    if TCP in packet:
        tcp = packet[TCP]
        logging.debug(f"TCP fields: {tcp.fields}")
    elif UDP in packet:
        udp = packet[UDP]
        logging.debug(f"UDP fields: {udp.fields}")

def ip_to_normalized_feature(ip_str):
    ip_int = int(ipaddress.ip_address(ip_str))
    return (ip_int / (2**32 - 1)) * 2 - 1  # Map to [-1, 1] range

def process_packet(packet):
    features = np.zeros(FEATURE_COUNT, dtype=np.float32)
    human_readable = {}

    try:
        if IP in packet:
            ip = packet[IP]
            inspect_packet(packet)  # Log detailed packet info for debugging
            
            try:
                # Convert IP addresses to normalized features
                features[0] = ip_to_normalized_feature(ip.src)
                features[1] = ip_to_normalized_feature(ip.dst)
                features[2] = (np.log1p(float(ip.len)) / np.log1p(65535)) * 2 - 1  # Map to [-1, 1]
                features[3] = int(ip.flags) / 3.5 - 1  # Map to [-1, 1] assuming max flag value is 7
                features[4] = float(ip.ttl) / 127.5 - 1  # Map to [-1, 1]
                features[5] = float(ip.proto) / 127.5 - 1  # Map to [-1, 1]
                human_readable.update({
                    'src_ip': ip.src,
                    'dst_ip': ip.dst,
                    'length': ip.len,
                    'flags': int(ip.flags),
                    'ttl': ip.ttl,
                    'protocol': protocol_map.get(ip.proto, "Unknown")
                })
            except Exception as e:
                logging.error(f"Error processing IP packet fields: {e}")
                return None, None, None

            if TCP in packet:
                tcp = packet[TCP]
                try:
                    features[6] = (np.log1p(float(tcp.sport)) / np.log1p(65535)) * 2 - 1
                    features[7] = (np.log1p(float(tcp.dport)) / np.log1p(65535)) * 2 - 1
                    features[8] = int(tcp.flags) / 127.5 - 1  # Map to [-1, 1]
                    features[9] = (np.log1p(float(tcp.window)) / np.log1p(65535)) * 2 - 1
                    features[10] = -1  # Placeholder for consistency
                    human_readable.update({
                        'src_port': tcp.sport,
                        'dst_port': tcp.dport,
                        'tcp_flags': int(tcp.flags),
                        'window': tcp.window
                    })
                except Exception as e:
                    logging.error(f"Error processing TCP packet fields: {e}")
                    return None, None, None
            elif UDP in packet:
                udp = packet[UDP]
                try:
                    features[6] = (np.log1p(float(udp.sport)) / np.log1p(65535)) * 2 - 1
                    features[7] = (np.log1p(float(udp.dport)) / np.log1p(65535)) * 2 - 1
                    features[8] = (np.log1p(float(udp.len)) / np.log1p(65535)) * 2 - 1
                    features[9] = -1  # Placeholder for consistency
                    features[10] = -1  # Placeholder for consistency
                    human_readable.update({
                        'src_port': udp.sport,
                        'dst_port': udp.dport,
                        'udp_len': udp.len
                    })
                except Exception as e:
                    logging.error(f"Error processing UDP packet fields: {e}")
                    return None, None, None
            else:
                features[6:11] = -1  # Set to -1 if neither TCP nor UDP
                human_readable.update({
                    'src_port': 0,
                    'dst_port': 0,
                    'flags_or_len': 0,
                    'window_or_padding': 0
                })

            features[11] = (np.log1p(float(len(packet))) / np.log1p(65535)) * 2 - 1
            human_readable['packet_size'] = len(packet)

            return features, human_readable, packet.time
        else:
            logging.warning("Packet does not contain IP layer")
            return None, None, None
    except Exception as e:
        logging.error(f"Unexpected error in process_packet: {e}", exc_info=True)
        return None, None, None

##################################################
# MODEL - thread 3
##################################################

model_ready_event = threading.Event()
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
    consumer_manager.consumer_config = consumer_manager.consumer_manager.consumer_config.copy()
    consumer_manager.consumer_config['auto.offset.reset'] = 'earliest'
    consumer = consumer_manager.get_consumer(TRAINING_TOPIC, f'training_group_{int(time.time())}', config=consumer_manager.consumer_config)

    data = []
    max_empty_polls = 5
    empty_poll_count = 0

    try:
        while empty_poll_count < max_empty_polls:
            msg = consumer.poll(1.0)
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
                logging.error(f"Unexpected error processing message fetching training data: {e}")

    finally:
        consumer_manager.close_consumer(consumer)
    
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
    max_retries = 30
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
    sequences = [packet['sequence'] for packet in data]
    
    for attempt in range(max_retries):
        try:
            url = f"{TORCHSERVE_REQUESTS_URL}/predictions/{MODEL_NAME}"
            headers = {'X-Request-Type': 'train'}
            
            logging.info(f"Sending model training request with {len(sequences)} sequences (attempt {attempt + 1})")
            response = requests.post(url, json=sequences, headers=headers)
            
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

def prediction_thread():
    logging.info("Starting prediction thread")
    consumer = consumer_manager.get_consumer(PROCESSED_TOPIC, 'prediction_group')

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
                logging.debug(f"Received message: {value}")
                if 'id' not in value or 'sequence' not in value:
                    logging.warning(f"Received message with missing 'id' or 'sequence': {value}")
                    continue
                
                if not isinstance(value['sequence'], list) or len(value['sequence']) != SEQUENCE_LENGTH:
                    logging.warning(f"Invalid sequence structure or length. Expected list of length {SEQUENCE_LENGTH}, got {type(value['sequence'])} of length {len(value['sequence'])}")
                    continue
                
                prediction = query_model(value['sequence'])
                
                if "error" in prediction:
                    logging.error(f"Error in prediction: {prediction['error']}")
                    continue
                
                anomaly_results = prediction.get('anomaly_results', [])
                if not anomaly_results:
                    logging.warning("No anomaly results in prediction")
                    continue

                for result in anomaly_results:
                    sequence_id = value['id']
                    sequence_human_readable = value.get('human_readable', [])
                    reconstruction_error = float(result['reconstruction_error'])
                    is_anomaly = reconstruction_error >= float(ANOMALY_THRESHOLD)
                    output = {
                        "id": sequence_id,
                        "reconstruction_error": reconstruction_error,
                        "is_anomaly": is_anomaly,
                        "human_readable": sequence_human_readable
                    }
                    logging.info(f'Producing prediction for sequence {sequence_id}: is_anomaly = {is_anomaly}, reconstruction_error = {reconstruction_error}')
                    producer.produce(PREDICTIONS_TOPIC, key=sequence_id, value=json.dumps(output))                
                producer.flush()
                logging.debug(f"Produced prediction for sequence {sequence_id}")
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding message: {e}")
            except Exception as e:
                logging.error(f"Unexpected error processing message in prediction thread: {e}", exc_info=True)

    except KeyboardInterrupt:
        logging.info("Prediction thread interrupted.")
    finally:
        consumer.close()
        logging.info("Prediction thread ended.")

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