import logging
import threading
import time
import os
from flask import Flask, jsonify, request, Response, current_app
from confluent_kafka import Consumer, KafkaException
from confluent_kafka.admin import AdminClient
from flask_cors import CORS
import requests
import json
from flask_socketio import SocketIO,emit

# ENVIRONMENT VARIABLES
KAFKA_BROKER = os.getenv('KAFKA_BROKER', 'kafka:9092')
RAW_TOPIC = os.getenv('RAW_TOPIC', 'raw_data')
PROCESSED_TOPIC = os.getenv('PROCESSED_TOPIC', 'processed_data')
PREDICTION_TOPIC = os.getenv('PREDICTION_TOPIC', 'predictions')
MAX_RETRIES = int(os.getenv('MAX_RETRIES', '5'))
RETRY_DELAY = int(os.getenv('RETRY_DELAY', '2'))
DATA_PROCESSING_URL = os.getenv('DATA_PROCESSING_URL', 'http://172.17.0.1:5001')

logging.basicConfig(level=logging.DEBUG)

class ConsumerManager:
    def __init__(self):
        self.consumers = {}
        self.lock = threading.Lock()

    def get_consumer(self, topic, group_id):
        with self.lock:
            key = f"{topic}-{group_id}"
            if key not in self.consumers:
                config = {
                    'bootstrap.servers': KAFKA_BROKER,
                    'auto.offset.reset': 'earliest'
                }
                config['group.id'] = group_id
                try:
                    self.consumers[key] = Consumer(config)
                    self.consumers[key].subscribe([topic])
                    logging.info(f"Successfully created consumer for topic: {topic}, group: {group_id}")
                except KafkaException as e:
                    logging.error(f"Failed to create consumer for topic {topic}, group {group_id}: {e}")
                    raise
            return self.consumers[key]

consumer_manager = ConsumerManager()
model_training = False

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
                time.sleep(retry_delay * (2 ** attempt))
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
                    time.sleep(retry_delay * (2 ** attempt))
                logging.debug(f"Topic {topic_name} does not exist.")
        except Exception as e:
            logging.error(f"Error checking topic existence: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (2 ** attempt))
    return False

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins="*")

@socketio.on('connect')
def handle_connect():
    logging.info("Client connected")
    threading.Thread(target=emit_health_status).start()
    threading.Thread(target=emit_raw_sample).start()
    threading.Thread(target=emit_processed_sample).start()
    threading.Thread(target=emit_anomaly_numbers).start()

# Add these functions to continuously emit data
def emit_health_status():
    with app.app_context():
        while True:
            try:
                health_status = {'status': 'OK'}  # Replace with actual health check
                socketio.emit('health_update', health_status)
            except Exception as e:
                logging.error(f"Error in emit_health_status: {e}")
            socketio.sleep(1)

def emit_raw_sample():
    with app.app_context():
        while True:
            try:
                sample = get_raw_sample()
                if sample:
                    socketio.emit('raw_sample_update', {
                        'time': sample['time'],
                        'data': sample['data'][:20] + '...'  # Truncate the data for display
                    })
            except Exception as e:
                logging.error(f"Error in emit_raw_sample: {e}")
            socketio.sleep(1)

def emit_processed_sample():
    with app.app_context():
        while True:
            try:
                sample = get_processed_sample()
                if sample:
                    socketio.emit('processed_sample_update', {
                        'timestamp': sample['timestamp'],
                        'features': sample['features']
                    })
            except Exception as e:
                logging.error(f"Error in emit_processed_sample: {e}")
            socketio.sleep(1)

def emit_anomaly_numbers():
    with app.app_context():
        while True:
            try:
                numbers = get_anomaly_numbers()
                if numbers:
                    socketio.emit('anomaly_numbers_update', numbers)
            except Exception as e:
                logging.error(f"Error in emit_anomaly_numbers: {e}")
            socketio.sleep(1)

def get_raw_sample():
    consumer = consumer_manager.get_consumer(RAW_TOPIC, 'raw_consumer_group')
    msg = consumer.poll(1.0)
    if msg is None or msg.error():
        return None
    return json.loads(msg.value().decode('utf-8'))

def get_processed_sample():
    consumer = consumer_manager.get_consumer(PROCESSED_TOPIC, 'processed_consumer_group')
    msg = consumer.poll(1.0)
    if msg is None or msg.error():
        return None
    return json.loads(msg.value().decode('utf-8'))

def get_anomaly_numbers():
    consumer = consumer_manager.get_consumer(PREDICTION_TOPIC, 'prediction_consumer_group')
    msg = consumer.poll(1.0)
    if msg is None or msg.error():
        return None
    prediction = json.loads(msg.value().decode('utf-8'))
    return {
        'total': 1,
        'normal': 0 if prediction['is_anomaly'] else 1,
        'anomalous': 1 if prediction['is_anomaly'] else 0
    }

def poll_model_status():
    while True:
        try:
            response = requests.get(f'{DATA_PROCESSING_URL}/status')
            if response.status_code == 200:
                status = response.json()
                socketio.emit('training_status_update', status)
                if status['status'] in ['completed', 'error']:
                    break
            else:
                socketio.emit('training_status_update', {"status": "error", "message": "Failed to fetch status"})
                break
        except requests.RequestException as e:
            socketio.emit('training_status_update', {"status": "error", "message": f"Error fetching status: {str(e)}"})
            break
        socketio.sleep(5)



@app.route('/train_model', methods=['POST'])
def train_model():
    data = request.json
    try:
        response = requests.post(f'{DATA_PROCESSING_URL}/train', json=data)
        if response.status_code == 202:
            threading.Thread(target=poll_model_status).start()
            return jsonify({"message": "Model training initiated"}), 202
        else:
            return jsonify({"message": "Failed to initiate model training"}), response.status_code
    except requests.RequestException as e:
        return jsonify({"message": f"Error communicating with model service: {str(e)}"}), 500

@app.route('/model_status', methods=['GET'])
def get_model_status():
    try:
        response = requests.get(f'{DATA_PROCESSING_URL}/status')
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({"status": "error", "message": "Failed to fetch status"}), 500
    except requests.RequestException as e:
        return jsonify({"status": "error", "message": f"Error fetching status: {str(e)}"}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', '5000'))
    logging.info(f"MLSEC web backend engine starting on 0.0.0.0:{port}")
    
    if not broker_accessible(KAFKA_BROKER):
        logging.error("Kafka broker is not accessible")
    if not topic_exists(KAFKA_BROKER, RAW_TOPIC) or not topic_exists(KAFKA_BROKER, PREDICTION_TOPIC) or not topic_exists(KAFKA_BROKER, PROCESSED_TOPIC):
        logging.error("Required Kafka topics not found")
    else:
        logging.info("Required Kafka topics are up")
    
    socketio.run(app, debug=True, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)