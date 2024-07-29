from datetime import datetime, timedelta
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

# GLOBAL VARIABLES
last_raw_data_time = None
last_processed_data_time = None
last_prediction_data_time = None
model_training = False
total_predictions = 0
normal_predictions = 0
anomalous_predictions = 0
logging.basicConfig(level=logging.DEBUG)


##########################################
# KAFKA UTILITY
##########################################


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

consumer_manager = ConsumerManager()


##########################################
# FLASK UTILITY
##########################################


app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins="*")

##########################################
# HANDLERS
##########################################

@socketio.on('connect')
def handle_connect():
    logging.info("Client connected")
    threading.Thread(target=emit_health_status).start()
    threading.Thread(target=emit_raw_sample).start()
    threading.Thread(target=emit_processed_sample).start()
    threading.Thread(target=emit_anomaly_numbers).start()
    threading.Thread(target=emit_data_flow_health).start()

@socketio.on('train_model')
def train_model(data):
    if 'startDate' not in data or 'endDate' not in data:
        emit('model_response', {"message": "startDate and endDate are required"}, broadcast=True)
        return

    try:
        response = requests.post(f'{DATA_PROCESSING_URL}/train', json=data)
        if response.status_code == 202:
            threading.Thread(target=get_model_status).start()
            emit('model_response', {"message": "Model training initiated"}, broadcast=True)
        else:
            emit('model_response', {"message": "Failed to initiate model training"}, broadcast=True)
    except requests.RequestException as e:
        emit('model_response', {"message": f"Error communicating with model service: {str(e)}"}, broadcast=True)

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

##########################################
# GETTERS
##########################################

def get_raw_sample():
    global last_raw_data_time
    consumer = consumer_manager.get_consumer(RAW_TOPIC, 'raw_consumer_group')
    msg = consumer.poll(1.0)
    if msg is None or msg.error():
        return None
    last_raw_data_time = datetime.now()
    return json.loads(msg.value().decode('utf-8'))

def get_processed_sample():
    global last_processed_data_time
    consumer = consumer_manager.get_consumer(PROCESSED_TOPIC, 'processed_consumer_group')
    msg = consumer.poll(1.0)
    if msg is None or msg.error():
        return None
    last_processed_data_time = datetime.now()
    return json.loads(msg.value().decode('utf-8'))

def get_anomaly_numbers():
    global last_prediction_data_time, total_predictions, normal_predictions, anomalous_predictions
    consumer = consumer_manager.get_consumer(PREDICTION_TOPIC, 'prediction_consumer_group')
    msg = consumer.poll(0.01)
    if msg is None or msg.error():
        return None
    last_prediction_data_time = datetime.now()
    prediction = json.loads(msg.value().decode('utf-8'))
    total_predictions += 1
    if prediction['is_anomaly']:
        anomalous_predictions += 1
    else:
        normal_predictions += 1
    return {
        'total': total_predictions,
        'normal': normal_predictions,
        'anomalous': anomalous_predictions
    }

def get_data_flow_health():
    current_time = datetime.now()
    threshold = timedelta(seconds=5)  # Adjust this value as needed
    
    def get_status_with_time(last_time):
        if last_time and (current_time - last_time) < threshold:
            return "healthy", last_time.isoformat()
        return "not receiving data", None

    raw_status, raw_time = get_status_with_time(last_raw_data_time)
    processed_status, processed_time = get_status_with_time(last_processed_data_time)
    prediction_status, prediction_time = get_status_with_time(last_prediction_data_time)
    
    return {
        "raw": {"status": raw_status, "last_update": raw_time},
        "processed": {"status": processed_status, "last_update": processed_time},
        "prediction": {"status": prediction_status, "last_update": prediction_time},
        "backend": {"status": "healthy", "last_update": current_time.isoformat()}
    }

def get_model_status():
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


##########################################
# EMITTERS
##########################################

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
    global total_predictions, normal_predictions, anomalous_predictions
    with app.app_context():
        while True:
            try:
                numbers = get_anomaly_numbers()
                if numbers:
                    socketio.emit('anomaly_numbers_update', numbers)
            except Exception as e:
                logging.error(f"Error in emit_anomaly_numbers: {e}")
            socketio.sleep(.01)

def emit_data_flow_health():
    with app.app_context():
        while True:
            try:
                health_status = get_data_flow_health()
                socketio.emit('data_flow_health_update', health_status)
            except Exception as e:
                logging.error(f"Error in emit_data_flow_health: {e}")
            socketio.sleep(5)


##########################################
# MAIN
##########################################


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