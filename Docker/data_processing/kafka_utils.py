import logging
import os
import threading
from confluent_kafka import Producer, Consumer, KafkaException


KAFKA_BROKER = os.getenv('KAFKA_BROKER', 'kafka:9092')

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
        self.lock = threading.Lock()
        self.consumer_config = config  # Store the base config

    def get_consumer(self, topic, group_id, config=None):
        with self.lock:
            config = config or self.consumer_config.copy()
            config['group.id'] = group_id
            try:
                consumer = Consumer(config)
                consumer.subscribe([topic])
                logging.info(f"Successfully created consumer for topic: {topic}, group: {group_id}")
                return consumer
            except KafkaException as e:
                logging.error(f"Failed to create consumer for topic {topic}, group {group_id}: {e}")
                raise

    def close_consumer(self, consumer):
        if consumer:
            consumer.close()
            logging.info("Consumer closed successfully")

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

def initialize_kafka_managers(kafka_broker):
    global producer_manager, consumer_manager
    producer_config['bootstrap.servers'] = kafka_broker
    consumer_config['bootstrap.servers'] = kafka_broker
    producer_manager = ProducerManager()
    consumer_manager = ConsumerManager(consumer_config)
    return producer_manager, consumer_manager