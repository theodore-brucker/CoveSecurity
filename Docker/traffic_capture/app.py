import datetime
import hashlib
import json
import os
import requests
import logging
from scapy.all import IP, TCP, ICMP, Ether, sniff
from confluent_kafka import Producer, Consumer, KafkaException, KafkaError
from confluent_kafka.admin import AdminClient
import threading
import time
from decimal import Decimal

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s',
                    handlers=[logging.StreamHandler()])

KAFKA_BROKER = os.getenv('KAFKA_BROKER', 'kafka:9092')

# Kafka Producer configuration
producer_config = {
    'bootstrap.servers': KAFKA_BROKER
}

# Kafka Consumer configuration
consumer_config = {
    'bootstrap.servers': KAFKA_BROKER,
    'group.id': 'network_data',
    'auto.offset.reset': 'earliest'
}

# Custom JSON Encoder
class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super(CustomEncoder, self).default(obj)

# Capture Network Traffic
def packet_callback(packet):
    return packet

def capture_network_traffic_from_file(file_path):
    logging.info(f"Reading from live capture file")
    while True:
        logging.debug(f"Checking if capture file exists: {file_path}")
        if not os.path.exists(file_path):
            logging.error(f"File not found: {file_path}. Retrying in 5 seconds...")
            time.sleep(5)
            continue

        logging.debug(f"Capturing network traffic from file: {file_path}")
        try:
            packets = sniff(offline=file_path, count=100)
            if packets:
                logging.debug(f"Captured {len(packets)} packets")
                produce_raw_data(packets)
            else:
                logging.info("No packets captured. Sleeping before retrying...")
                time.sleep(5)  # Wait before trying again
        except FileNotFoundError:
            logging.error(f"File not found: {file_path}. Retrying in 5 seconds...")
            time.sleep(5)
        except Exception as e:
            logging.error(f"Error capturing traffic: {e}. Retrying in 5 seconds...")
            time.sleep(5)

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
def produce_raw_data(packets):
    logging.debug("Producing raw data to Kafka")
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
        logging.debug("Finished producing raw data to Kafka")
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
        logging.debug("Non-IP packet received, cannot process")
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
def process_raw_data(broker_address):
    logging.info("Starting to process raw data")
    while not topic_exists(broker_address, "raw_data"):
        logging.warning("raw_data topic not found. Sleeping for 10 seconds and retrying...")
        time.sleep(10)
    
    consumer = Consumer(consumer_config)
    try:
        logging.info("Subscribing to raw_data topic")
        consumer.subscribe(["raw_data"])
        
        producer = Producer(producer_config)
        while True:
            msg = consumer.poll(10)  # Increase the poll timeout to 10 seconds
            if msg is None:
                continue
            elif msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                else:
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
                        producer.flush()
                        consumer.commit(msg)
                except Exception as e:
                    logging.debug(f"Failed to process packet: {e}")
    except Exception as e:
        logging.error(f"Failed to process raw data: {e}")
    finally:
        consumer.close()

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
def consume_processed_data_and_query_model(broker_address):
    logging.info("Starting to consume processed data and query model")
    while not topic_exists(broker_address, "processed_data"):
        logging.warning("processed_data topic not found. Sleeping for 10 seconds and retrying...")
        time.sleep(10)
    
    consumer = Consumer(consumer_config)
    try:
        logging.info("Subscribing to processed_data topic")
        consumer.subscribe(["processed_data"])
        
        producer = Producer(producer_config)
        while True:
            msg = consumer.poll(10)  # Increase the poll timeout to 10 seconds
            if msg is None:
                continue
            elif msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                else:
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
                    producer.flush()
                    consumer.commit(msg)
    except Exception as e:
        logging.error(f"Failed to consume processed data and query model: {e}")
    finally:
        consumer.close()

def main():
    logging.info("Application started")
    try:
        broker_address = KAFKA_BROKER
    except Exception as e:
        logging.error(f"Failed to connect to Kafka broker: {e}")
        return

    # Job 1: Capture network traffic from the file and produce raw data to Kafka
    capture_thread = threading.Thread(target=capture_network_traffic_from_file, args=("/mnt/capture/traffic.pcap",))
    capture_thread.start()

    # Job 2: Process raw data and produce to processed_data topic
    process_thread = threading.Thread(target=process_raw_data, args=(broker_address,))
    process_thread.start()

    # Job 3: Consume processed data, query TorchServe, and produce predictions
    consume_thread = threading.Thread(target=consume_processed_data_and_query_model, args=(broker_address,))
    consume_thread.start()

    # Wait for all threads to finish
    capture_thread.join()
    process_thread.join()
    consume_thread.join()

if __name__ == "__main__":
    main()

