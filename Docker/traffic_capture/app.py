import hashlib
import json
import os
import pickle
import pandas as pd
import requests
import logging
from scapy.all import IP, TCP, UDP, ICMP, Ether, sniff, raw
from confluent_kafka import Producer, Consumer, KafkaException, KafkaError
from confluent_kafka.admin import AdminClient
import threading
import time
from decimal import Decimal

KAFKA_BROKER = os.getenv('KAFKA_BROKER', 'kafka:9092')
RAW_TOPIC = 'raw_data'
PROCESSED_TOPIC = 'processed_data'
PREDICTIONS_TOPIC = 'predictions'
MODEL_PATH = '/app/model/'
APP_PATH = '/app/'
SCALER_PATH = 'model/minmaxscaler.pkl'

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

# Load the custom min max scaler determined during model training
with open(SCALER_PATH, 'rb') as f:
            scaler = pickle.load(f)

# Custom JSON encoder for better process handling
class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super(CustomEncoder, self).default(obj)

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
                    except Exception as e:
                        logging.error("Thread 2: Failed to process packet: {}".format(e))
    except Exception as e:
        logging.error("Thread 2: Failed to consume or produce messages: {}".format(e))
    finally:
        consumer.close()
        producer.flush()

def scale_features(features, scaler):
    try:
        scaled = scaler.transform([features])[0]
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

# Function consumes from the processed data topic and queries the model, producing the responses to the predictions topic
def query_model(broker_address):
    logging.info(f"Thread 3: Connecting to {PROCESSED_TOPIC}")
    while not topic_exists(broker_address, PROCESSED_TOPIC):
        logging.warning(f"Thread 3: {PROCESSED_TOPIC} topic not found. Sleeping for 10 seconds and retrying...")
        time.sleep(10)
    logging.info(f"Thread 3: Connected to {PROCESSED_TOPIC}")

    logging.info(f"Thread 3: Connecting to {PREDICTIONS_TOPIC}")
    while not topic_exists(broker_address, PREDICTIONS_TOPIC):
        logging.warning(f"Thread 3: {PREDICTIONS_TOPIC} topic not found. Sleeping for 10 seconds and retrying...")
        time.sleep(10)
    logging.info(f"Thread 3: Connected to {PREDICTIONS_TOPIC}")

    consumer = Consumer(consumer_config)
    producer = Producer(producer_config)
    try:
        consumer.subscribe([PROCESSED_TOPIC])
        
        while True:
            msg = consumer.poll(10)
            if msg is None:
                continue
            elif msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                else:
                    logging.error(f"Thread 3: Error consuming message: {msg.error()}")
            else:
                key = msg.key().decode("utf8")
                value = json.loads(msg.value())
                try:
                    logging.info("Thread 3: Querying model")
                    model_response = query_torchserve(value)
                    if model_response:
                        producer.produce(
                            topic=PREDICTIONS_TOPIC,
                            key=key,
                            value=json.dumps(model_response, cls=CustomEncoder),
                        )
                        producer.flush()
                        consumer.commit(msg)
                        logging.info("Thread 3: Queried model")
                except Exception as e:
                    logging.error(f"Thread 3: Failed to produce prediction: {e}")
    except Exception as e:
        logging.error(f"Thread 3: Failed to consume processed data and query model: {e}")
    finally:
        consumer.close()
        producer.flush()

# Handler function that passes each request to TorchServe
def query_torchserve(data, retries=5, delay=5):
    url = "http://torchserve:8080/predictions/autoencoder"
    logging.info(f"Querying TorchServe model at {url} with data: {data}")
    
    for attempt in range(retries):
        try:
            response = requests.post(url, json=data)
            response.raise_for_status()  # Raise an error for bad status codes
            result = response.json()
            logging.info(f"Received model response: {result}")
            return result
        except requests.exceptions.HTTPError as errh:
            logging.error(f"HTTP Error: {errh}")
        except requests.exceptions.ConnectionError as errc:
            logging.error(f"Error Connecting: {errc}")
        except requests.exceptions.Timeout as errt:
            logging.error(f"Timeout Error: {errt}")
        except requests.exceptions.RequestException as err:
            logging.error(f"Request Exception: {err}")
        
        logging.info(f"Retrying in {delay} seconds...")
        time.sleep(delay)
    
    logging.error(f"Failed to connect to TorchServe after {retries} attempts")
    return None



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

    # Job 1: Capture network traffic from the file and produce raw data to Kafka
    logging.info("Thread 1: Starting thread for raw data")
    capture_thread = threading.Thread(target=capture_network_traffic_from_file, args=("/mnt/capture/traffic.pcap",broker_address))
    capture_thread.start()

    # Job 2: Process raw data and produce to processed_data topic
    logging.info("Thread 2: Starting thread for processed data")
    process_thread = threading.Thread(target=process_raw_data, args=(broker_address,))
    process_thread.start()

    logging.info("Thread 3: Starting thread for model queries")
    # Job 3: Consume processed data, query TorchServe, and produce predictions
    consume_thread = threading.Thread(target=query_model, args=(broker_address,))
    consume_thread.start()

    # Wait for all threads to finish
    capture_thread.join()
    process_thread.join()
    consume_thread.join()

if __name__ == "__main__":
    main()