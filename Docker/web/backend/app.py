from datetime import datetime, timedelta
from decimal import Decimal
import logging
import threading
import time
import os
import uuid
from flask import Flask, jsonify, request
from confluent_kafka import Consumer, KafkaException, Producer
from confluent_kafka.admin import AdminClient
from flask_cors import CORS
import numpy as np
import requests
import json
from flask_socketio import SocketIO
from werkzeug.utils import secure_filename

# ENVIRONMENT VARIABLES
KAFKA_BROKER = os.getenv('KAFKA_BROKER', 'kafka:9092')
RAW_TOPIC = os.getenv('RAW_TOPIC', 'raw_data')
PROCESSED_TOPIC = os.getenv('PROCESSED_TOPIC', 'processed_data')
PREDICTION_TOPIC = os.getenv('PREDICTION_TOPIC', 'predictions')
TRAINING_TOPIC = os.getenv('TRAINING_TOPIC', 'training_data')
TRAINING_DATA_PATH = os.getenv('TRAINING_DATA_PATH', '/app/training_data')
LABELED_DATA_TOPIC = os.getenv('LABELED_DATA_TOPIC', 'labeled_data')

MAX_RETRIES = int(os.getenv('MAX_RETRIES', '5'))
RETRY_DELAY = int(os.getenv('RETRY_DELAY', '2'))
DATA_PROCESSING_URL = os.getenv('DATA_PROCESSING_URL', 'http://172.17.0.1:5001')
ALLOWED_EXTENSIONS = {'pcap'}

SEQUENCE_LENGTH = int(os.getenv('SEQUENCE_LENGTH', '16'))
FEATURE_COUNT = int(os.getenv('FEATURE_COUNT', '12'))


# GLOBAL VARIABLES
last_raw_data_time = None
last_processed_data_time = None
last_prediction_data_time = None
last_training_data_time = None
model_training = False
total_predictions = 0
normal_predictions = 0
anomalous_predictions = 0
anomalous_sequences = []
logging.basicConfig(level=logging.INFO)

##########################################
# PCAP UPLOADER
##########################################

logging.info(f'TRAINING_DATA_PATH: {TRAINING_DATA_PATH}')
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


##########################################
# KAFKA UTILITY
##########################################


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

producer_manager = ProducerManager()
consumer_manager = ConsumerManager(consumer_config)

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

##########################################
# FLASK UTILITY
##########################################


from scapy.fields import EDecimal
from datetime import datetime
class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        try:
            if obj is None:
                return None
            elif isinstance(obj, (Decimal, EDecimal)):
                return str(obj)
            elif isinstance(obj, bytes):
                return obj.decode('utf-8', errors='replace')
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            elif isinstance(obj, (np.integer, np.floating)):
                return obj.item()
            elif isinstance(obj, datetime):
                return obj.isoformat()
            elif isinstance(obj, complex):
                return [obj.real, obj.imag]
            elif isinstance(obj, set):
                return list(obj)
            elif isinstance(obj, uuid.UUID):
                return str(obj)
            elif hasattr(obj, '__str__'):
                return str(obj)
            return super(CustomEncoder, self).default(obj)
        except Exception as e:
            logging.error(f"Error in CustomEncoder: {e} for object type {type(obj)}", exc_info=True)
            return str(obj)  # Fall back to string representation
        
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins="*")

producer = producer_manager.get_producer(LABELED_DATA_TOPIC)

def delivery_report(err, msg):
    if err is not None:
        logging.error(f'Message delivery failed: {err}')
    else:
        logging.info(f'Message delivered to {msg.topic()} [{msg.partition()}]')

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
    threading.Thread(target=emit_anomalous_sequences).start()

@app.route('/training_start', methods=['POST'])
def start_training():
    try:
        response = requests.post(f'{DATA_PROCESSING_URL}/training_start')
        if response.status_code == 200:
            return jsonify({"message": "Training job started"}), 200
        else:
            return jsonify({"error": "Failed to start training job"}), 500
    except requests.RequestException as e:
        return jsonify({"error": f"Error communicating with data processing service: {str(e)}"}), 500

@app.route('/training_data', methods=['POST'])
def training_data():
    if 'file' in request.files:
            file = request.files['file']
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                filepath = os.path.join(TRAINING_DATA_PATH, filename)
                logging.info(f'Saving file to {filepath}')
                file.save(filepath)
                logging.info(f'Contents of {TRAINING_DATA_PATH} after save: {os.listdir(TRAINING_DATA_PATH)}')
                data = {'file_path': filename}
                training_type = 'pcap'
            else:
                logging.error('Invalid file type')
                return jsonify({"error": "Invalid file type"}), 400
    elif 'startDate' in request.form and 'endDate' in request.form:
        try:
            start_date = int(request.form['startDate'])
            end_date = int(request.form['endDate'])
            data = {
                'startDate': start_date,
                'endDate': end_date
            }
            training_type = 'time_window'
        except ValueError:
            return jsonify({"error": "Invalid date format. Expecting Unix timestamps."}), 400
    else:
        return jsonify({"error": "Missing required data"}), 400

    try:
        response = requests.post(f'{DATA_PROCESSING_URL}/train_data', json=data)
        if response.status_code == 202:
            job_info = response.json()
            job_id = job_info.get('job_id')
            threading.Thread(target=emit_job_status, args=(job_id,)).start()
            return jsonify({
                "message": f"Data processing initiated with {training_type}",
                "job_id": job_id
            }), 202
        else:
            return jsonify({"error": "Failed to initiate data processing"}), 500
    except requests.RequestException as e:
        return jsonify({"error": f"Error communicating with data processing service: {str(e)}"}), 500

def emit_job_status(job_id=None):
    with app.app_context():
        while True:
            try:
                if job_id:
                    response = requests.get(f'{DATA_PROCESSING_URL}/status/{job_id}')
                    event_name = 'job_status_update'
                else:
                    response = requests.get(f'{DATA_PROCESSING_URL}/status')
                    event_name = 'training_status_update'
                
                if response.status_code == 200:
                    status = response.json()
                    socketio.emit(event_name, status)
                    if status['status'] in ['completed', 'error']:
                        break
                else:
                    socketio.emit(event_name, {
                        "status": "error",
                        "message": "Failed to fetch status",
                        "job_id": job_id
                    })
                    break
            except requests.RequestException as e:
                socketio.emit(event_name, {
                    "status": "error",
                    "message": f"Error fetching status: {str(e)}",
                    "job_id": job_id
                })
                break
            socketio.sleep(5)  # Check status every 5 seconds

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

@app.route('/anomalous_sequences', methods=['GET'])
def get_paginated_anomalous_sequences():
    page = int(request.args.get('page', 1))
    per_page = 5
    start = (page - 1) * per_page
    end = start + per_page
    sequences = anomalous_sequences[start:end]
    return jsonify({
        'sequences': sequences,
        'total': len(anomalous_sequences),
        'page': page,
        'per_page': per_page
    })

@app.route('/mark_as_normal', methods=['POST'])
def mark_as_normal():
    data = request.json
    _id = data.get('_id')
    if not _id:
        return jsonify({"error": "Missing _id"}), 400

    update_data = {
        "timestamp": datetime.now(),  # Current timestamp as datetime object
        "sequence": None,  # We don't have the sequence data here, so set to None
        "is_training": False,
        "human_readable": None,  # We don't have this data, so set to None
        "is_anomaly": False,  # Since we're marking it as normal, set this to False
        "reconstruction_error": None,  # We don't have this data, so set to None
        "is_false_positive": True  # This field is not in the schema, but we'll keep it for now
    }
    
    try:
        producer.produce(
            LABELED_DATA_TOPIC,
            key=str(_id),
            value=json.dumps(update_data, cls=CustomEncoder),
            callback=delivery_report
        )
        producer.flush()
        return jsonify({"message": "Sequence marked as normal"}), 200
    except Exception as e:
        logging.error(f"Error updating sequence {_id}: {e}")
        return jsonify({"error": "Failed to mark sequence as normal"}), 500

@app.route('/train_with_labeled_data', methods=['POST'])
def train_with_labeled_data():
    try:
        # Send a request to the data processing service to start training with labeled data
        response = requests.post(f'{DATA_PROCESSING_URL}/train_with_labeled_data')
        if response.status_code == 200:
            return jsonify({"message": "Training with labeled data initiated"}), 200
        else:
            return jsonify({"error": "Failed to initiate training"}), 500
    except requests.RequestException as e:
        return jsonify({"error": f"Error initiating training: {str(e)}"}), 500

##########################################
# GETTERS
##########################################

def validate_data_size(data):
    if len(data['sequence']) != SEQUENCE_LENGTH:
        logging.error(f"Invalid sequence length: {len(data['sequence'])} != {SEQUENCE_LENGTH}")
        return False
    if len(data['sequence'][0]) != FEATURE_COUNT:
        logging.error(f"Invalid feature count: {len(data['sequence'][0])} != {FEATURE_COUNT}")
        return False
    return True

def get_anomalous_sequences():
    global anomalous_sequences, last_prediction_data_time
    consumer = consumer_manager.get_consumer(PREDICTION_TOPIC, 'anomalous_prediction_consumer_group')
    anomalous_sequences = []
    while True:
        msg = consumer.poll(0.01)
        if msg is None or msg.error():
            break
        last_prediction_data_time = datetime.now()
        prediction = json.loads(msg.value().decode('utf-8'))
        if prediction.get('is_anomaly'):
            anomalous_sequences.append({
                'id': prediction['id'],
                'reconstruction_error': prediction['reconstruction_error'],
                'human_readable': prediction['human_readable']
            })
    return anomalous_sequences

def get_raw_sample():
    global last_raw_data_time
    consumer = consumer_manager.get_consumer(RAW_TOPIC, 'raw_consumer_group')
    msg = consumer.poll(5.0)
    if msg is None or msg.error():
        logging.error(f"Error fetching raw sample: {msg.error() if msg else 'No message'}")
        return None
    last_raw_data_time = datetime.now()
    try:
        value = json.loads(msg.value().decode('utf-8'))
        logging.debug(f"Human readable features: {value.get('human_readable', {})}")
        if value and validate_data_size(value):
            return {
                'id': value['id'],
                'timestamp': datetime.fromisoformat(value['timestamp']) if isinstance(value['timestamp'], str) else value['timestamp'],
                'sequence': value['sequence'],
                'human_readable': value.get('human_readable', {})
            }
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logging.error(f"Error parsing raw sample: {e}")
        return None

def get_processed_sample():
    global last_processed_data_time
    consumer = consumer_manager.get_consumer(PROCESSED_TOPIC, 'processed_consumer_group')
    msg = consumer.poll(5.0)
    if msg is None or msg.error():
        logging.error(f"Error fetching processed sample: {msg.error() if msg else 'No message'}")
        return None
    last_processed_data_time = datetime.now()
    try:
        value = json.loads(msg.value().decode('utf-8'))
        logging.debug(f"Human readable features: {value.get('human_readable', {})}")
        if value and validate_data_size(value):
            return {
                'id': value['id'],
                'timestamp': datetime.fromisoformat(value['timestamp']) if isinstance(value['timestamp'], str) else value['timestamp'],
                'sequence': value['sequence'],
                'human_readable': value.get('human_readable', {})  # Ensure human_readable is included
            }
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logging.error(f"Error parsing processed sample: {e}")
        return None

def get_training_sample():
    global last_training_data_time
    consumer_config = consumer_manager.consumer_config.copy()
    consumer_config['auto.offset.reset'] = 'earliest'
    consumer = consumer_manager.get_consumer(TRAINING_TOPIC, 'training_group', consumer_config)
    max_empty_polls = 5
    empty_poll_count = 0
    while empty_poll_count < max_empty_polls:
        msg = consumer.poll(5.0)
        if msg is None or msg.error():
            return None
        last_training_data_time = datetime.now()
        empty_poll_count = 0
        try:
            value = json.loads(msg.value().decode('utf-8'))
            logging.debug(f"Human readable features: {value.get('human_readable', {})}")
            if value and validate_data_size(value):
                return {
                    'id': value['id'],
                    'timestamp': datetime.fromisoformat(value['timestamp']) if isinstance(value['timestamp'], str) else value['timestamp'],
                    'sequence': value['sequence'],
                    'human_readable': value['human_readable']
                }
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logging.error(f"Error parsing training sample: {e}")
            return None

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
    training_data_status, training_data_time = get_status_with_time(last_training_data_time)
    
    return {
        "raw": {"status": raw_status, "last_update": raw_time},
        "processed": {"status": processed_status, "last_update": processed_time},
        "training": {"status": training_data_status, "last_update": training_data_time},
        "prediction": {"status": prediction_status, "last_update": prediction_time},
        "backend": {"status": "healthy", "last_update": current_time.isoformat()}
    }

##########################################
# EMITTERS
##########################################

def emit_anomalous_sequences():
    with app.app_context():
        while True:
            try:
                anomalous_sequences = get_anomalous_sequences()
                socketio.emit('anomalous_sequences_update', {
                    'total': len(anomalous_sequences),
                    'sequences': anomalous_sequences[-5:]  # Send only the 5 most recent anomalous sequences
                })
            except Exception as e:
                logging.error(f"Error in emit_anomalous_sequences: {e}")
            socketio.sleep(1)

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
                        'id': sample['id'],
                        'timestamp': sample['timestamp'],
                        'sequence': sample['sequence'],
                        'human_readable': sample['human_readable']
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
                        'id': sample['id'],
                        'timestamp': sample['timestamp'],
                        'sequence': sample['sequence'],
                        'human_readable': sample['human_readable']
                    })
            except Exception as e:
                logging.error(f"Error in emit_processed_sample: {e}")
            socketio.sleep(1)

def emit_training_sample():
    with app.app_context():
        while True:
            try:
                sample = get_training_sample()
                if sample:
                    socketio.emit('processed_training_update', {
                        'id': sample['id'],
                        'timestamp': sample['timestamp'],
                        'sequence': sample['sequence'],
                        'human_readable': sample['human_readable']
                    })
            except Exception as e:
                logging.error(f"Error in emit_training_sample: {e}")
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