from datetime import datetime, timedelta
from decimal import Decimal
import logging
import sys
import threading
import time
import os
import traceback
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
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError, ConfigurationError
from bson import json_util, ObjectId

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

MONGO_URI = os.getenv('MONGO_URI', "mongodb://mongodb:27017/")
DB_NAME = "network_sequences"
try:
    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client[DB_NAME]
    sequences_collection = db.sequences
except ConfigurationError as e:
    logging.error(f"MongoDB configuration error: {e}")
    exit(1)

# GLOBAL VARIABLES
last_raw_data_time = None
last_processed_data_time = None
last_prediction_data_time = None
last_training_data_time = None
model_training = False
total_predictions = 0
normal_predictions = 0
anomalous_predictions = 0
anomalous_sequences = []  # In-memory storage for anomalous sequences
false_positive_sequences = []  # New list to store false positives
logging.basicConfig(level=logging.INFO)

training_status = {
    'status': 'idle',
    'progress': 0,
    'message': 'Waiting to start...'
}

##########################################
# PCAP UPLOADER
##########################################

logging.info(f'TRAINING_DATA_PATH: {TRAINING_DATA_PATH}')
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


##########################################
# KAFKA AND MONGO UTILITY
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
producer = producer_manager.get_producer(LABELED_DATA_TOPIC)

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

def check_mongo_connection():
    try:
        # Attempt to fetch server info
        mongo_client.server_info()
        # Check if we can access the collection
        sequences_collection.find_one()
        logging.info("MongoDB connection successful")
        return True
    except ConnectionFailure:
        logging.error("MongoDB connection failed")
    except ServerSelectionTimeoutError:
        logging.error("MongoDB server selection timeout")
    except Exception as e:
        logging.error(f"Unexpected error when connecting to MongoDB: {str(e)}")
    return False

def delivery_report(err, msg):
    if err is not None:
        logging.error(f'Message delivery failed: {err}')
    else:
        logging.info(f'Message delivered to {msg.topic()} [{msg.partition()}]')


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

##########################################
# HANDLERS
##########################################


def update_training_status(new_status):
    global training_status
    training_status.update(new_status)
    logging.info(f"Training status updated: {training_status}")
    socketio.emit('training_status_update', training_status)

@socketio.on('anomalous_sequences_update')
def handle_anomalous_sequences_update(data):
    global anomalous_sequences
    anomalous_sequences.extend(data['sequences'])
    # Optionally, limit the size of anomalous_sequences to prevent memory issues
    anomalous_sequences = anomalous_sequences[-1000:]  # Keep only the last 1000 sequences

@app.route('/api/anomalous_sequences', methods=['GET'])
def get_anomalous_sequences():
    logging.info("Received request for anomalous sequences")
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 10))
    start = (page - 1) * per_page
    end = start + per_page
    logging.info(f"Fetching anomalous sequences: page={page}, per_page={per_page}, start={start}, end={end}")
    
    # Filter out sequences that are in false_positive_sequences
    filtered_sequences = [seq for seq in anomalous_sequences if seq not in false_positive_sequences]
    
    logging.info(f"Total anomalous sequences (excluding false positives): {len(filtered_sequences)}")
    sequences = filtered_sequences[start:end]
    logging.info(f"Returning {len(sequences)} sequences")
    return jsonify({
        'data': sequences,
        'total_pages': (len(filtered_sequences) + per_page - 1) // per_page,
        'current_page': page
    })

@app.route('/api/mongodb_data', methods=['GET'])
def get_mongodb_data():
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 10))
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    query = {}
    if start_date:
        query['timestamp'] = {'$gte': datetime.fromisoformat(start_date)}
    if end_date:
        query['timestamp'] = query.get('timestamp', {})
        query['timestamp']['$lte'] = datetime.fromisoformat(end_date)

    total_count = db.sequences.count_documents(query)
    data = list(db.sequences.find(query).skip((page - 1) * per_page).limit(per_page))

    return jsonify({
        'data': json.loads(json_util.dumps(data)),
        'total_pages': (total_count + per_page - 1) // per_page,
        'current_page': page
    })

@socketio.on('connect')
def handle_connect():
    logging.info("Client connected")
    socketio.emit('training_status_update', training_status)
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
            update_training_status({
                'status': 'in_progress',
                'progress': 0,
                'message': 'Training job started.'
            })
            job_id = response.json().get('job_id')
            threading.Thread(target=emit_job_status, args=(job_id,)).start()
            return jsonify({"message": "Training job started"}), 200
        else:
            update_training_status({
                'status': 'error',
                'progress': 0,
                'message': 'Failed to start training job.'
            })
            return jsonify({"error": "Failed to start training job"}), 500
    except requests.RequestException as e:
        update_training_status({
            'status': 'error',
            'progress': 0,
            'message': f'Error communicating with data processing service: {str(e)}'
        })
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

@app.route('/model_status', methods=['GET'])
def get_model_status():
    try:
        response = requests.get(f'{DATA_PROCESSING_URL}/status')
        if response.status_code == 200:
            status = response.json()
            update_training_status(status)
            return jsonify(status)
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
    logging.info(f"Received request to mark sequence {_id} as normal")
    
    if not _id:
        logging.warning("Request to mark as normal received without _id")
        return jsonify({"error": "Missing _id"}), 400

    # Find the sequence in anomalous_sequences
    sequence = next((seq for seq in anomalous_sequences if seq['_id'] == _id), None)
    if not sequence:
        logging.warning(f"Sequence {_id} not found in anomalous sequences")
        return jsonify({"error": "Sequence not found in anomalous sequences"}), 404

    # Remove from anomalous_sequences and add to false_positive_sequences
    anomalous_sequences[:] = [seq for seq in anomalous_sequences if seq['_id'] != _id]
    false_positive_sequences.append(sequence)
    
    logging.info(f"Sequence {_id} removed from anomalous sequences and added to false positives")
    logging.info(f"Current anomalous sequences count: {len(anomalous_sequences)}")
    logging.info(f"Current false positive sequences count: {len(false_positive_sequences)}")

    update_data = {
        "timestamp": datetime.now(),
        "sequence": sequence['sequence'],
        "is_training": False,
        "human_readable": sequence.get('human_readable'),
        "is_anomaly": False,
        "reconstruction_error": sequence.get('reconstruction_error'),
        "is_false_positive": True
    }
    
    try:
        producer.produce(
            LABELED_DATA_TOPIC,
            key=str(_id),
            value=json.dumps(update_data, cls=CustomEncoder),
            callback=delivery_report
        )
        producer.flush()
        logging.info(f"Sequence {_id} successfully marked as normal and sent to Kafka topic")
        return jsonify({"message": "Sequence marked as normal (false positive)"}), 200
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

@app.route('/api/data', methods=['GET'])
def get_data():
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 10))

        logging.info(f"Received request with params: start_date={start_date}, end_date={end_date}, page={page}, per_page={per_page}")

        query = {}
        if start_date:
            start = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            query['timestamp'] = query.get('timestamp', {})
            query['timestamp']['$gte'] = start

        if end_date:
            end = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            end = end + timedelta(days=1)  # Include the entire last day
            query['timestamp'] = query.get('timestamp', {})
            query['timestamp']['$lt'] = end

        logging.info(f"Constructed query: {query}")

        total_count = sequences_collection.count_documents(query)
        logging.info(f"Total documents matching query: {total_count}")

        total_pages = (total_count + per_page - 1) // per_page

        data = list(sequences_collection.find(query, {'_id': 1, 'timestamp': 1})
                    .skip((page - 1) * per_page)
                    .limit(per_page))

        logging.info(f"Retrieved {len(data)} documents")

        response_data = {
            'data': json.loads(json_util.dumps(data)),
            'total_pages': total_pages,
            'current_page': page
        }
        logging.info(f"Sending response: {response_data}")

        return jsonify(response_data)
    except Exception as e:
        logging.error(f"Error querying MongoDB: {e}", exc_info=True)
        return jsonify({"error": "Failed to retrieve data"}), 500

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
                '_id': value['_id'],
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
                '_id': value['_id'],
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
                    '_id': value['_id'],
                    'timestamp': datetime.fromisoformat(value['timestamp']) if isinstance(value['timestamp'], str) else value['timestamp'],
                    'sequence': value['sequence'],
                    'human_readable': value['human_readable']
                }
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logging.error(f"Error parsing training sample: {e}")
            return None

def get_anomaly_numbers():
    global last_prediction_data_time, total_predictions, normal_predictions, anomalous_predictions, anomalous_sequences, false_positive_sequences
    consumer = consumer_manager.get_consumer(PREDICTION_TOPIC, 'prediction_consumer_group')
    msg = consumer.poll(0.1)
    if msg is None or msg.error():
        return None
    last_prediction_data_time = datetime.now()
    prediction = json.loads(msg.value().decode('utf-8'))
    total_predictions += 1
    if prediction['is_anomaly']:
        anomalous_predictions += 1
        new_anomaly = {
            '_id': prediction.get('_id', str(uuid.uuid4())),
            'timestamp': prediction.get('timestamp', datetime.now().isoformat()),
            'sequence': prediction.get('sequence', []),
            'reconstruction_error': prediction.get('reconstruction_error', 0),
            'human_readable': prediction.get('human_readable', {})
        }
        if new_anomaly not in false_positive_sequences:
            anomalous_sequences.append(new_anomaly)
            logging.info(f"New anomalous sequence added with ID: {new_anomaly['_id']}")
        else:
            logging.info(f"Sequence {new_anomaly['_id']} not added to anomalous sequences (marked as false positive)")
        # Limit the size of anomalous_sequences to prevent memory issues
        if len(anomalous_sequences) > 1000:
            removed_sequence = anomalous_sequences.pop(0)
            logging.info(f"Removed oldest anomalous sequence with ID: {removed_sequence['_id']}")
        logging.info(f"Anomalous sequence added. Total anomalous sequences: {len(anomalous_sequences)}")
    else:
        normal_predictions += 1
    
    logging.info(f"Current prediction stats - Total: {total_predictions}, Normal: {normal_predictions}, Anomalous: {anomalous_predictions}")
    return {
        'total': total_predictions,
        'normal': normal_predictions,
        'anomalous': anomalous_predictions
    }

def get_data_flow_health():
    current_time = datetime.now()
    threshold = timedelta(seconds=1) 
    
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

##########################################
# EMITTERS
##########################################

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
                    update_training_status(status)
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
            socketio.sleep(1)

def emit_anomalous_sequences():
    with app.app_context():
        while True:
            try:
                logging.info(f"Current anomalous sequences: {len(anomalous_sequences)}")
                socketio.emit('anomalous_sequences_update', {
                    'total': len(anomalous_sequences),
                    'sequences': anomalous_sequences[-5:]  # Send only the 5 most recent anomalous sequences
                })
            except Exception as e:
                logging.error(f"Error in emit_anomalous_sequences: {e}")
                logging.error(traceback.format_exc())
            socketio.sleep(1)

def emit_health_status():
    with app.app_context():
        while True:
            try:
                health_status = {'status': 'OK'}
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
                        '_id': sample['_id'],
                        'timestamp': str(sample['timestamp']),
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
                        '_id': sample['_id'],
                        'timestamp': str(sample['timestamp']),
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
                        '_id': sample['_id'],
                        'timestamp': str(sample['timestamp']),
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
                    socketio.emit('traffic_ratio_update', {
                        'normal_count': numbers['normal'],
                        'anomalous_count': numbers['anomalous'],
                        'total_count': numbers['total']
                    })
            except Exception as e:
                logging.error(f"Error in emit_anomaly_numbers: {e}")
            socketio.sleep(0.1)  # Control update frequency

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

logging.basicConfig(level=logging.DEBUG, stream=sys.stdout,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

if __name__ == '__main__':
    port = int(os.getenv('PORT', '5000'))
    logging.info(f"Cove Security web backend engine starting on 0.0.0.0:{port}")
    
    if not broker_accessible(KAFKA_BROKER):
        logging.error("Kafka broker is not accessible")
    if not topic_exists(KAFKA_BROKER, RAW_TOPIC) or not topic_exists(KAFKA_BROKER, PREDICTION_TOPIC) or not topic_exists(KAFKA_BROKER, PROCESSED_TOPIC):
        logging.error("Required Kafka topics not found")
    else:
        logging.info("Required Kafka topics are up")
    
    # Check MongoDB connection
    if check_mongo_connection():
        socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
    else:
        logging.error("Failed to connect to MongoDB. Exiting.")
        exit(1)