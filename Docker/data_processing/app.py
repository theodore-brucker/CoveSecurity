from datetime import datetime, timezone
import json
import os
import uuid
import numpy as np
import pandas as pd
import requests
import logging
from decimal import Decimal
from scapy.fields import EDecimal
from scapy.all import IP, TCP, UDP, ICMP, sniff, rdpcap
from confluent_kafka import Producer, Consumer, KafkaException
from confluent_kafka.admin import AdminClient
import threading
import time
from flask import Flask, jsonify, request
from sklearn.preprocessing import MinMaxScaler, StandardScaler
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
SCALER_PATH = os.getenv('SCALER_PATH', '/app/scaler_data/robust_scaler.pkl')
TRAINING_DATA_PATH = os.getenv('TRAINING_DATA_PATH', '/app/training_data')
ANOMALY_THRESHOLD = float(os.getenv('ANOMALY_THRESHOLD', 1))
SEQUENCE_LENGTH = int(os.getenv('SEQUENCE_LENGTH', 16))
FEATURE_COUNT = int(os.getenv('FEATURE_COUNT', 12))

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

class PacketSequenceBuffer:
    def __init__(self, sequence_length=SEQUENCE_LENGTH, feature_dim=FEATURE_COUNT):
        self.sequence_length = sequence_length
        self.feature_dim = feature_dim
        self.feature_buffer = []
        self.human_readable_buffer = []
        self.scalers = [StandardScaler() for _ in range(feature_dim)]
        logging.info(f"Initialized PacketSequenceBuffer with sequence_length={sequence_length}, feature_dim={feature_dim}")

    def add_packet(self, packet_features, human_readable):
        logging.debug(f"Adding packet to buffer. Current buffer size: {len(self.feature_buffer)}")
        if len(packet_features) != self.feature_dim:
            logging.warning(f"Packet features dimension mismatch. Expected {self.feature_dim}, got {len(packet_features)}. Padding or truncating.")
            packet_features = packet_features[:self.feature_dim] + [0] * (self.feature_dim - len(packet_features))
        self.feature_buffer.append(packet_features)
        self.human_readable_buffer.append(human_readable)
        if len(self.feature_buffer) == self.sequence_length:
            logging.debug("Buffer full, processing sequence")
            feature_sequence = self._scale_data(self.feature_buffer)
            human_readable_sequence = self.human_readable_buffer.copy()
            self.feature_buffer.clear()
            self.human_readable_buffer.clear()
            return feature_sequence, human_readable_sequence
        return None, None

    def _scale_data(self, data):
        logging.debug("Scaling data")
        try:
            num_samples, feat_dim = len(data), len(data[0])
            scaled_data = np.zeros((num_samples, feat_dim))

            for i in range(feat_dim):
                feature_data = np.array([packet[i] for packet in data]).reshape(-1, 1)
                if np.all(feature_data == feature_data[0]):
                    logging.warning(f"Feature {i} has zero variance. Skipping scaling for this feature.")
                    scaled_data[:, i] = feature_data.reshape(-1)
                else:
                    scaled_feature = self.scalers[i].fit_transform(feature_data)
                    scaled_data[:, i] = scaled_feature.reshape(-1)

            logging.debug(f"Scaled data shape: {scaled_data.shape}")
            return scaled_data.tolist()
        except Exception as e:
            logging.error(f"Error during data scaling: {e}")
            return data

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

def is_full_sequence(sequence):
    if len(sequence) == SEQUENCE_LENGTH:
        return True
    logging.warning(f'sequence of length {len(sequence)} did not meet desired length {SEQUENCE_LENGTH}')
    return False

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
        def packet_callback(packet):
            try:
                logging.debug(f"[TrafficCaptureThread] Captured packet type: {type(packet)}")
                logging.debug(f"[TrafficCaptureThread] Packet summary: {packet.summary()}")
                features, human_readable = process_packet(packet)
                if features:
                    feature_sequence, human_readable_sequence = packet_buffer.add_packet(features, human_readable)
                    if feature_sequence:
                        is_training = is_training_period and datetime.now(timezone.utc) <= training_end_time
                        produce_raw_data([feature_sequence], [human_readable_sequence], is_training)
            except Exception as e:
                logging.error(f"[TrafficCaptureThread] Error processing individual packet: {str(e)}", exc_info=True)

        logging.info(f"[TrafficCaptureThread] Starting packet capture on interface: {interface}")
        sniff(iface=interface, prn=packet_callback, store=0)
    except ValueError as ve:
        logging.error(f"[TrafficCaptureThread] ValueError in capture_live_traffic: {str(ve)}", exc_info=True)
        logging.error(f"[TrafficCaptureThread] Interface value: {interface}")
    except Exception as e:
        logging.error(f"[TrafficCaptureThread] Error capturing live traffic: {str(e)}", exc_info=True)

def produce_raw_data(feature_sequences, human_readable_sequences, is_training=False):
    producer = producer_manager.get_producer(RAW_TOPIC)
    logging.debug(f'Attempting to produce {len(feature_sequences)} raw sequences')
    for feature_sequence, human_readable_sequence in zip(feature_sequences, human_readable_sequences):
        logging.debug(f"Processing sequence. Type: {type(feature_sequence)}, Length: {len(feature_sequence)}")
        logging.debug(f"Sequence content: {feature_sequence}")
        
        if not isinstance(feature_sequence, list):
            logging.warning(f"feature_sequence is not a list. Type: {type(feature_sequence)}. Attempting to convert.")
            try:
                feature_sequence = list(feature_sequence)
            except Exception as e:
                logging.error(f"Failed to convert feature_sequence to list: {e}")
                continue
        logging.debug(f"Feature length: {feature_sequence} Desired: {SEQUENCE_LENGTH}")
        if len(feature_sequence) != SEQUENCE_LENGTH:
            logging.warning(f"Skipping sequence of length {len(feature_sequence)}")
            continue

        if not all(isinstance(packet, list) and len(packet) == FEATURE_COUNT for packet in feature_sequence):
            logging.warning(f"Invalid packet structure in sequence. Expected {SEQUENCE_LENGTH} packets, each with {FEATURE_COUNT} features.")
            continue

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
            logging.debug(f"Serialized sequence: {serialized_sequence}")
            
            json_data = json.dumps(serialized_sequence, cls=CustomEncoder)
            producer.produce(
                RAW_TOPIC,
                key=serialized_sequence['id'],
                value=json_data
            )
            producer.poll(0)
            logging.debug(f"Produced sequence with ID {serialized_sequence['id']}")
        except Exception as e:
            logging.error(f"Error producing sequence: {e}", exc_info=True)

    try:
        producer.flush()
        logging.debug('Finished producing all sequences.')
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
            features, human_readable = process_packet(packet)
            if features:
                feature_sequence, human_readable_sequence = sequence_buffer.add_packet(features, human_readable)
                if feature_sequence:
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

def fit_scaler(sample_size=5):
    consumer = consumer_manager.get_consumer(RAW_TOPIC, 'scaler_fitting')
    features_list = []
    while len(features_list) < sample_size:
        msg = consumer.poll(1.0)
        if msg is None:
            continue
        if msg.error():
            logging.error(f"Consumer error while fitting scaler: {msg.error()}")
            continue

        try:
            value = json.loads(msg.value())
            logging.debug(f"Received message for scaler fitting: {value}")
            for packet_features in value['sequence']:
                if len(packet_features) == FEATURE_COUNT and all(isinstance(f, (int, float)) for f in packet_features):
                    features_list.append(packet_features)
                    if len(features_list) >= sample_size:
                        break
                else:
                    logging.warning(f"Invalid packet features: {packet_features}")
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding message: {e}")
        except Exception as e:
            logging.error(f"Unexpected error processing message during scaler fit: {e}")

    if features_list and all(len(features) == FEATURE_COUNT for features in features_list):
        column_names = ['src_ip', 'dst_ip', 'length', 'flags', 'ttl', 'protocol', 'src_port', 'dst_port', 'tcp_flags', 'window', 'packet_size', 'padding']
        df = pd.DataFrame(features_list, columns=column_names)
        
        df = df.apply(pd.to_numeric, errors='coerce')
        df = df.dropna()
        
        if df.empty:
            logging.error("DataFrame is empty after dropping NaN values")
            raise ValueError("Insufficient or incorrect feature data to create DataFrame")

        scaler = StandardScaler()
        scaler.fit(df)
        logging.info("Successfully fitted StandardScaler")
        return scaler, column_names, features_list
    else:
        logging.error("Insufficient or incorrect feature data to create DataFrame")
        raise ValueError("Insufficient or incorrect feature data to create DataFrame")

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

def scale_features(features, scaler, column_names):
    try:
        # Convert features to a DataFrame using the same column names used for fitting
        df = pd.DataFrame([features], columns=column_names)
        
        # Scale the features using the provided scaler
        scaled = scaler.transform(df)
        logging.debug(f"Scaled features: {scaled}")
        
        return scaled[0]  # Return the first (and only) row as a list of scaled features
    except Exception as e:
        logging.error(f"Error scaling features: {e}")
        return None

def process_packet(packet):
    logging.debug(f"Processing packet: {packet.summary()}")
    try:
        if isinstance(packet, tuple):
            packet = packet[0]
            logging.debug("Unpacked packet from tuple")

        features = []
        human_readable = {}

        # IP features
        if IP in packet:
            ip = packet[IP]
            logging.debug(f"Processing IP packet: src={ip.src}, dst={ip.dst}")
            try:
                features.extend([
                    int.from_bytes(bytes(map(int, ip.src.split('.'))), byteorder='big'),
                    int.from_bytes(bytes(map(int, ip.dst.split('.'))), byteorder='big'),
                    safe_convert(ip.len),
                    safe_convert(ip.flags),
                    safe_convert(ip.frag),
                    safe_convert(ip.ttl),
                    safe_convert(ip.proto)
                ])
                human_readable.update({
                    'src_ip': ip.src,
                    'dst_ip': ip.dst,
                    'length': ip.len,
                    'flags': ip.flags,
                    'frag': ip.frag,
                    'ttl': ip.ttl,
                    'protocol': protocol_map.get(ip.proto, "Unknown")
                })
            except Exception as e:
                logging.warning(f"Error processing IP features: {e}")
                features.extend([0] * 7)
        else:
            logging.debug("Packet does not contain IP layer")
            features.extend([0] * 7)

        # TCP/UDP features
        if TCP in packet:
            tcp = packet[TCP]
            logging.debug(f"Processing TCP packet: sport={tcp.sport}, dport={tcp.dport}")
            try:
                features.extend([
                    safe_convert(tcp.sport),
                    safe_convert(tcp.dport),
                    safe_convert(tcp.flags),
                    safe_convert(tcp.window)
                ])
                human_readable.update({
                    'src_port': tcp.sport,
                    'dst_port': tcp.dport,
                    'tcp_flags': tcp.flags,
                    'window': tcp.window
                })
            except Exception as e:
                logging.warning(f"Error processing TCP features: {e}")
                features.extend([0] * 4)
        elif UDP in packet:
            udp = packet[UDP]
            logging.debug(f"Processing UDP packet: sport={udp.sport}, dport={udp.dport}")
            try:
                features.extend([
                    safe_convert(udp.sport),
                    safe_convert(udp.dport),
                    safe_convert(udp.len),
                    0  # Padding to match TCP feature count
                ])
                human_readable.update({
                    'src_port': udp.sport,
                    'dst_port': udp.dport,
                    'udp_len': udp.len
                })
            except Exception as e:
                logging.warning(f"Error processing UDP features: {e}")
                features.extend([0] * 4)
        else:
            logging.debug("Packet does not contain TCP or UDP layer")
            features.extend([0] * 4)

        # General packet features
        features.append(safe_convert(len(packet)))
        human_readable['packet_size'] = len(packet)

        # Ensure we have the correct number of features
        if len(features) < FEATURE_COUNT:
            logging.warning(f"Not enough features: {len(features)}. Padding with zeros.")
            features.extend([0] * (FEATURE_COUNT - len(features)))
        elif len(features) > FEATURE_COUNT:
            logging.warning(f"Too many features: {len(features)}. Truncating.")
            features = features[:FEATURE_COUNT]

        logging.debug(f"Processed packet features: {features}")
        logging.debug(f"Human readable info: {human_readable}")
        return features, human_readable
    except Exception as e:
        logging.error(f"Failed to process packet: {e}")
        return [0] * FEATURE_COUNT, {}

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
                logging.error(f"Unexpected error processing message fetching training data: {e}")

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