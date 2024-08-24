import logging
import os
import time
import threading
from datetime import datetime, timezone
import json
import uuid
import numpy as np
from scapy.all import IP, TCP, UDP, sniff, rdpcap
import netifaces
from sklearn.preprocessing import RobustScaler
from utils import CustomEncoder
from status_utils import update_training_status
from kafka_utils import initialize_kafka_managers
from processing_utils import process_packet

SEQUENCE_LENGTH = int(os.getenv('SEQUENCE_LENGTH', 16))
FEATURE_COUNT = int(os.getenv('FEATURE_COUNT', 12))
RAW_TOPIC = os.getenv('RAW_TOPIC', 'raw_data')
KAFKA_BROKER = os.getenv('KAFKA_BROKER', 'kafka:9092')
producer_manager,_ = initialize_kafka_managers(KAFKA_BROKER)
is_training_period = False
training_end_time = None

##################################################
# TRAFFIC CAPTURE - thread 1
##################################################

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
            
            # Convert numpy.int64 to float64 before calculating mean
            inter_packet_times = inter_packet_times.astype(np.float64)
            avg_inter_packet_time = np.mean(inter_packet_times)
            
            # Normalize the average inter-packet time (example normalization, adjust as needed)
            normalized_avg_time = (np.log1p(avg_inter_packet_time) / np.log1p(1)) * 2 - 1  # Assuming 1 second as max
            
            # Replace the 4th feature (index 3) with the new inter-packet time feature
            self.feature_buffer[:, 3] = normalized_avg_time

            scaled_sequence = self.global_scaler.transform(self.feature_buffer)
            
            # Add debugging information here
            logging.debug("Scaled sequence statistics:")
            for i in range(self.feature_dim):
                feature_column = scaled_sequence[:, i]
                logging.debug(f"Feature {i}: min={feature_column.min():.4f}, max={feature_column.max():.4f}, "
                              f"mean={feature_column.mean():.4f}, std={feature_column.std():.4f}")
            
            # Additional overall statistics
            logging.debug(f"Overall: min={scaled_sequence.min():.4f}, max={scaled_sequence.max():.4f}, "
                          f"mean={scaled_sequence.mean():.4f}, std={scaled_sequence.std():.4f}")

            human_readable_sequence = self.human_readable_buffer.copy()
            for hr in human_readable_sequence:
                hr['avg_inter_packet_time'] = float(avg_inter_packet_time)  # Convert to float

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

def numpy_to_python(obj):
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj

def produce_raw_data(feature_sequences, human_readable_sequences, is_training=False):
    producer = producer_manager.get_producer(RAW_TOPIC)
    logging.debug(f'Attempting to produce {len(feature_sequences)} raw sequences')
    valid_sequences = 0
    
    for idx, (feature_sequence, human_readable_sequence) in enumerate(zip(feature_sequences, human_readable_sequences)):
        logging.debug(f"Processing sequence {idx}. Type: {type(feature_sequence)}, Length: {len(feature_sequence) if isinstance(feature_sequence, (list, np.ndarray)) else 'N/A'}")
        
        # Ensure feature_sequence is a list of Python native types
        feature_sequence = [numpy_to_python(packet) for packet in feature_sequence]
        
        # Convert human_readable_sequence to Python native types
        human_readable_sequence = [
            {k: numpy_to_python(v) for k, v in packet.items()}
            for packet in human_readable_sequence
        ]
        
        if len(feature_sequence) != SEQUENCE_LENGTH:
            logging.warning(f"Skipping sequence {idx} of length {len(feature_sequence)}. Expected {SEQUENCE_LENGTH}")
            continue
        
        # Check packet structure
        if not all(isinstance(packet, list) and len(packet) == FEATURE_COUNT for packet in feature_sequence):
            logging.warning(f"Invalid packet structure in sequence {idx}. Expected {SEQUENCE_LENGTH} packets, each with {FEATURE_COUNT} features.")
            continue
        
        # Check human_readable_sequence length
        if len(human_readable_sequence) != SEQUENCE_LENGTH:
            logging.warning(f"Human readable sequence length mismatch in sequence {idx}. Expected {SEQUENCE_LENGTH}, got {len(human_readable_sequence)}")
            continue
        
        try:
            serialized_sequence = {
                "id": generate_unique_id(),
                "timestamp": time.time(),
                "sequence": feature_sequence,
                "is_training": is_training,
                "human_readable": human_readable_sequence
            }
            
            logging.debug(f"Serializing sequence {idx}")
            json_data = json.dumps(serialized_sequence, cls=CustomEncoder)
            logging.debug(f"Successfully serialized sequence {idx}")
            
            producer.produce(
                RAW_TOPIC,
                key=serialized_sequence['id'],
                value=json_data
            )
            producer.poll(0)
            valid_sequences += 1
        except Exception as e:
            logging.error(f"Error producing sequence {idx}: {e}", exc_info=True)
    
    try:
        producer.flush()
        logging.debug(f'Finished producing {valid_sequences} out of {len(feature_sequences)} sequences.')
    except Exception as e:
        logging.error(f"Error flushing producer: {e}", exc_info=True)

def generate_unique_id():
    return str(uuid.uuid4())

def read_pcap(file_path, is_training=True):
    update_training_status("Training data upload", 0, "Initiated file upload")
    logging.info(f"Processing uploaded PCAP file: {file_path}")
    try:
        update_training_status("Training data upload", 10, "Reading from file")
        packets = rdpcap(file_path)
        update_training_status("Training data upload", 20, "Successfully read from file")
        logging.info(f"Successfully unpacked {len(packets)} packets from training file")

        sequence_buffer = PacketSequenceBuffer()
        feature_sequences = []
        human_readable_sequences = []
        update_training_status("Training data upload", 30, "Processing data")
        for i, packet in enumerate(packets):
            features, human_readable, timestamp = process_packet(packet)
            if features is not None:
                logging.debug(f"Processing packet {i}: features={features}, timestamp={timestamp}")
                feature_sequence, human_readable_sequence = sequence_buffer.add_packet(features, human_readable, timestamp)
                if feature_sequence is not None:
                    feature_sequences.append(feature_sequence)
                    human_readable_sequences.append(human_readable_sequence)
        update_training_status("Training data upload", 50, "Successfully processed data")

        logging.info(f"Processed {len(feature_sequences)} sequences")
        if feature_sequences:
            logging.debug(f"Sample feature sequence: {feature_sequences[0][:5]}...")  # Show first 5 elements
            logging.debug(f"Sample human readable sequence: {human_readable_sequences[0][0]}")  # Show first packet in sequence

        update_training_status("Training data upload", 60, "Producing data")
        if feature_sequences:
            produce_raw_data(feature_sequences, human_readable_sequences, is_training)
        update_training_status("Training data upload", 80, "Successfully produced data")
        
        update_training_status("Training data upload", 100, "Successfully uploaded training data")
    except Exception as e:
        update_training_status("Training data upload", 0, "Failed to upload training data")
        logging.error(f"Error processing uploaded PCAP: {e}", exc_info=True)