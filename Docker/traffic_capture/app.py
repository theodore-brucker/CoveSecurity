import datetime
import hashlib
import json
import os
import requests
import logging
from scapy.all import IP, TCP, ICMP, Ether, sniff
from quixstreams import Application
from confluent_kafka.admin import AdminClient
import threading
import time
from decimal import Decimal

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s',
                    handlers=[logging.StreamHandler()])

# Environment variable for Kafka broker
KAFKA_BROKER = 'localhost:9092'

# Capture Network Traffic
def packet_callback(packet):
    return packet

def capture_network_traffic_from_file(file_path, app):
    while True:
        logging.info(f"Checking if capture file exists: {file_path}")
        if not os.path.exists(file_path):
            logging.error(f"File not found: {file_path}. Retrying in 5 seconds...")
            time.sleep(5)
            continue

        logging.info(f"Capturing network traffic from file: {file_path}")
        try:
            packets = sniff(offline=file_path, count=10)
            if packets:
                logging.info(f"Captured {len(packets)} packets")
                produce_raw_data(app, packets)
            else:
                logging.info("No packets captured. Sleeping before retrying...")
                time.sleep(5)  # Wait before trying again
        except FileNotFoundError:
            logging.error(f"File not found: {file_path}. Retrying in 5 seconds...")
            time.sleep(5)
        except Exception as e:
            logging.error(f"Error capturing traffic: {e}. Retrying in 5 seconds...")
            time.sleep(5)

# Custom JSON Encoder
class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super(CustomEncoder, self).default(obj)

# Serialize packet
def serialize_packet(packet):
    try:
        return {
            "time": packet.time,
            "summary": packet.summary()
        }
    except Exception as e:
        logging.error(f"Error serializing packet: {e}")
        return None

# Produce Raw Data to Kafka
def produce_raw_data(app, packets):
    logging.info("Producing raw data to Kafka")
    try:
        with app.get_producer() as producer:
            for packet in packets:
                serialized_packet = serialize_packet(packet)
                if serialized_packet:
                    producer.produce(
                        topic="raw_data",
                        key=str(packet.time),
                        value=json.dumps(serialized_packet, cls=CustomEncoder)
                    )
        logging.info("Finished producing raw data to Kafka")
    except Exception as e:
        logging.error(f"Failed to produce raw data: {e}")

# Process Packet
def process_packet(packet_summary):
    def ip_to_hash(ip: str) -> int:
        return int(hashlib.sha256(ip.encode()).hexdigest()[:8], 16)

    def flags_to_int(flags: str) -> int:
        return sum((0x01 << i) for i, f in enumerate('FSRPAUEC') if f in flags)

    def protocol_to_int(proto: int) -> int:
        # Simple mapping for common protocol numbers to a smaller range
        protocol_map = {1: 1, 6: 2, 17: 3, 2: 4}  # ICMP, TCP, UDP, IGMP
        return protocol_map.get(proto, 0)  # Default to 0 if unknown

    try:
        packet = Ether(packet_summary["summary"].encode())
    except Exception as e:
        logging.error(f"Error converting packet summary to Ether object: {e}")
        return None

    if IP in packet:
        ip_layer = packet[IP]
        tcp_layer = packet[TCP] if TCP in packet else None
        icmp_layer = packet[ICMP] if ICMP in packet else None

        timestamp = datetime.datetime.now().timestamp()  # Add timestamp as the first feature
        features = [
            timestamp,  # Unscaled timestamp
            ip_to_hash(ip_layer.src) if ip_layer else ip_to_hash("0.0.0.0"),
            ip_to_hash(ip_layer.dst) if ip_layer else ip_to_hash("0.0.0.0"),
            ip_layer.ttl if ip_layer else 0,
            protocol_to_int(ip_layer.proto) if ip_layer else 0,
            tcp_layer.sport if tcp_layer else 0,
            tcp_layer.dport if tcp_layer else 0,
            flags_to_int(tcp_layer.flags) if tcp_layer and hasattr(tcp_layer, 'flags') else 0,
            len(packet),  # Using packet length as a feature
        ]

        if icmp_layer:
            features.extend([icmp_layer.type, icmp_layer.code])
        else:
            features.extend([0, 0])  # Default to 0 if not ICMP

        return features
    else:
        logging.error("Non-IP packet received, cannot process")
        return None

# Check if topic exists using Kafka admin client
def topic_exists(broker_address, topic_name):
    try:
        admin_client = AdminClient({'bootstrap.servers': broker_address})
        metadata = admin_client.list_topics(timeout=10)
        return topic_name in metadata.topics
    except Exception as e:
        logging.error(f"Error checking topic existence: {e}")
        return False

# Process Raw Data and Produce to Processed Data Topic
def process_raw_data(app, broker_address):
    logging.info("Starting to process raw data")
    while not topic_exists(broker_address, "raw_data"):
        logging.warning("raw_data topic not found. Sleeping for 10 seconds and retrying...")
        time.sleep(10)
    
    try:
        with app.get_consumer() as consumer:
            logging.info("Subscribing to raw_data topic")
            consumer.subscribe(["raw_data"])
            logging.info("Subscription to raw_data topic successful")
            
            with app.get_producer() as producer:
                while True:
                    msg = consumer.poll(10)  # Increase the poll timeout to 10 seconds
                    if msg is None:
                        continue
                    elif msg.error() is not None:
                        logging.error(f"Error consuming message: {msg.error()}")
                    else:
                        key = msg.key().decode("utf8")
                        value = json.loads(msg.value())
                        offset = msg.offset()
                        partition = msg.partition()

                        try:
                            processed_value = process_packet(value)
                            if processed_value:
                                producer.produce(
                                    topic="processed_data",
                                    key=key,
                                    value=json.dumps(processed_value, cls=CustomEncoder),
                                )
                                consumer.store_offsets(msg)
                        except Exception as e:
                            logging.error(f"Failed to process packet: {e}")
        logging.info("Finished processing raw data")
    except Exception as e:
        logging.error(f"Failed to process raw data: {e}")

# Query TorchServe Model
def query_torchserve(data):
    logging.info(f"Querying TorchServe model with data: {data}")
    response = requests.post("http://torchserve:8081/predictions/autoencoder", json=data)
    if response.status_code == 200:
        result = response.json()
        logging.info(f"Received model response: {result}")
        return result
    else:
        logging.error(f"Failed to query TorchServe model: {response.status_code} {response.text}")
        return None

# Consume Processed Data, Query Model, and Produce Predictions
def consume_processed_data_and_query_model(app, broker_address):
    logging.info("Starting to consume processed data and query model")
    while not topic_exists(broker_address, "processed_data"):
        logging.warning("processed_data topic not found. Sleeping for 10 seconds and retrying...")
        time.sleep(10)
    
    try:
        with app.get_consumer() as consumer:
            logging.info("Subscribing to processed_data topic")
            consumer.subscribe(["processed_data"])
            logging.info("Subscription to processed_data topic successful")
            
            with app.get_producer() as producer:
                while True:
                    msg = consumer.poll(10)  # Increase the poll timeout to 10 seconds
                    if msg is None:
                        continue
                    elif msg.error() is not None:
                        logging.error(f"Error consuming message: {msg.error()}")
                    else:
                        key = msg.key().decode("utf8")
                        value = json.loads(msg.value())
                        offset = msg.offset()
                        partition = msg.partition()

                        model_response = query_torchserve(value)
                        if model_response:
                            producer.produce(
                                topic="predictions",
                                key=key,
                                value=json.dumps(model_response, cls=CustomEncoder),
                            )
                            consumer.store_offsets(msg)
        logging.info("Finished consuming processed data and querying model")
    except Exception as e:
        logging.error(f"Failed to consume processed data and query model: {e}")

def main():
    logging.info("Application started")
    try:
        broker_address = KAFKA_BROKER
        app = Application(broker_address=broker_address, loglevel="DEBUG", auto_offset_reset="earliest", consumer_group="network_data")
    except Exception as e:
        logging.error(f"Failed to connect to Kafka broker: {e}")
        return

    # Step 1: Capture network traffic from the file and produce raw data to Kafka
    capture_thread = threading.Thread(target=capture_network_traffic_from_file, args=("/mnt/capture/traffic.pcap", app))
    capture_thread.start()

    # Step 2: Process raw data and produce to processed_data topic
    process_thread = threading.Thread(target=process_raw_data, args=(app, broker_address))
    process_thread.start()

    # Step 3: Consume processed data, query TorchServe, and produce predictions
    consume_thread = threading.Thread(target=consume_processed_data_and_query_model, args=(app, broker_address))
    consume_thread.start()

    # Wait for all threads to finish
    capture_thread.join()
    process_thread.join()
    consume_thread.join()

if __name__ == "__main__":
    main()
