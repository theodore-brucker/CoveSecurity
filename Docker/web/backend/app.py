import logging
import time
from flask import Flask, jsonify, request
from confluent_kafka import Consumer, KafkaException
from confluent_kafka.admin import AdminClient
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Kafka configuration
KAFKA_BROKER = 'kafka:9092'
RAW_TOPIC = 'raw_data'
PROCESSED_TOPIC = 'processed_data'
PREDICTION_TOPIC = 'predictions'
MAX_RETRIES = 5
RETRY_DELAY = 2  # in seconds

# Configure logging
logging.basicConfig(level=logging.DEBUG)

def broker_accessible(broker_address, max_retries=MAX_RETRIES, retry_delay=RETRY_DELAY):
    for attempt in range(max_retries):
        try:
            logging.debug("Checking broker accessibility...")
            admin_client = AdminClient({'bootstrap.servers': broker_address})
            admin_client.list_topics(timeout=10)
            logging.debug("Broker is accessible.")
            return True
        except Exception as e:
            logging.error(f"Error accessing broker: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (2 ** attempt))  # Exponential backoff
    return False

def topic_exists(broker_address, topic_name, max_retries=MAX_RETRIES, retry_delay=RETRY_DELAY):
    for attempt in range(max_retries):
        try:
            logging.debug(f"Checking if topic {topic_name} exists...")
            admin_client = AdminClient({'bootstrap.servers': broker_address})
            metadata = admin_client.list_topics(timeout=10)
            if topic_name in metadata.topics:
                logging.debug(f"Topic {topic_name} exists.")
                return True
            else:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))  # Exponential backoff
                logging.debug(f"Topic {topic_name} does not exist.")
        except Exception as e:
            logging.error(f"Error checking topic existence: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (2 ** attempt))  # Exponential backoff
    return False

# Check broker and topic
if not broker_accessible(KAFKA_BROKER):
    raise Exception("Kafka broker is not accessible")
if not topic_exists(KAFKA_BROKER, RAW_TOPIC) or not topic_exists(KAFKA_BROKER, PREDICTION_TOPIC):
    raise Exception("Required Kafka topics do not exist")

# Initialize Kafka consumers
raw_consumer = Consumer({
    'bootstrap.servers': KAFKA_BROKER,
    'group.id': 'raw_consumer_group',
    'auto.offset.reset': 'earliest'
})

processed_consumer = Consumer({
    'bootstrap.servers': KAFKA_BROKER,
    'group.id': 'processed_consumer_group',
    'auto.offset.reset': 'earliest'
})

prediction_consumer = Consumer({
    'bootstrap.servers': KAFKA_BROKER,
    'group.id': 'prediction_consumer_group',
    'auto.offset.reset': 'earliest'
})

# Subscribe to topics
raw_consumer.subscribe([RAW_TOPIC])
processed_consumer.subscribe([PROCESSED_TOPIC])
prediction_consumer.subscribe([PREDICTION_TOPIC])

@app.route('/ratio', methods=['GET'])
def get_ratio():
    anomalous_count = 0
    normal_count = 0

    try:
        while True:
            msg = prediction_consumer.poll(1.0)
            if msg is None:
                break
            if msg.error():
                if msg.error().code() == KafkaException._PARTITION_EOF:
                    break
                else:
                    logging.error(msg.error())
                    continue

            prediction = msg.value().decode('utf-8')
            if prediction == 'anomalous':
                anomalous_count += 1
            else:
                normal_count += 1

    except Exception as e:
        logging.error(f"Error consuming messages: {e}")

    ratio = anomalous_count / (normal_count + anomalous_count) if (normal_count + anomalous_count) > 0 else 0
    return jsonify({'ratio': ratio, 'anomalous': anomalous_count, 'normal': normal_count})

@app.route('/raw_sample', methods=['GET'])
def get_raw_sample():
    try:
        msg = raw_consumer.poll(1.0)
        if msg is None or msg.error():
            return jsonify({'error': 'No messages in raw_data topic'}), 404
        sample = {'key': msg.key().decode('utf-8'), 'value': msg.value().decode('utf-8')}
    except Exception as e:
        logging.error(f"Error consuming messages: {e}")
        return jsonify({'error': 'Failed to fetch sample from raw_data topic'}), 500

    return jsonify(sample)

@app.route('/processed_sample', methods=['GET'])
def get_processed_sample():
    try:
        msg = processed_consumer.poll(1.0)
        if msg is None or msg.error():
            return jsonify({'error': 'No messages in processed_data topic'}), 404
        sample = {'key': msg.key().decode('utf-8'), 'value': msg.value().decode('utf-8')}
    except Exception as e:
        logging.error(f"Error consuming messages: {e}")
        return jsonify({'error': 'Failed to fetch sample from processed_data topic'}), 500

    return jsonify(sample)

@app.route('/prediction_sample', methods=['GET'])
def get_prediction_sample():
    try:
        msg = prediction_consumer.poll(1.0)
        if msg is None or msg.error():
            return jsonify({'error': 'No messages in predictions topic'}), 404
        sample = {'key': msg.key().decode('utf-8'), 'value': msg.value().decode('utf-8')}
    except Exception as e:
        logging.error(f"Error consuming messages: {e}")
        return jsonify({'error': 'Failed to fetch sample from predictions topic'}), 500

    return jsonify(sample)

# Health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
