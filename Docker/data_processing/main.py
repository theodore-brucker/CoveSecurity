from datetime import datetime, timezone
import json
import os
import sys
import requests
import logging
from scapy.all import IP, TCP, UDP
from confluent_kafka import KafkaException
import threading
import time
from flask import Flask, jsonify, request
from kafka_utils import initialize_kafka_managers
from traffic_capture import (
    read_pcap, 
    process_time_window, 
    get_available_interfaces, 
    capture_live_traffic
)
from utils import CustomEncoder
from status_utils import update_training_status, get_training_status

APP_PATH = os.getenv('APP_PATH', '/app/')
KAFKA_BROKER = os.getenv('KAFKA_BROKER', 'kafka:9092')
RAW_TOPIC = os.getenv('RAW_TOPIC', 'raw_data')
PROCESSED_TOPIC = os.getenv('PROCESSED_TOPIC', 'processed_data')
PREDICTIONS_TOPIC = os.getenv('PREDICTIONS_TOPIC', 'predictions')
TRAINING_TOPIC = os.getenv('TRAINING_TOPIC', 'training_data')
LABELED_TOPIC = os.getenv('LABELED_TOPIC', 'labeled_data')
FLASK_PORT = int(os.getenv('FLASK_PORT', 5001))
CAPTURE_INTERFACE = os.getenv('CAPTURE_INTERFACE', 'eth0')
TORCHSERVE_REQUESTS_URL = os.getenv('TORCHSERVE_REQUESTS', 'http://localhost:8080')
TORCHSERVE_MANAGEMENT_URL = os.getenv('TORCHSERVE_MANAGEMENT', 'http://localhost:8081')
TORCHSERVE_METRICS_URL = os.getenv('TORCHSERVE_METRICS', 'http://localhost:8082')
MODEL_NAME = os.getenv('MODEL_NAME', 'transformer_autoencoder')
TRAINING_DATA_PATH = os.getenv('TRAINING_DATA_PATH', '/app/training_data')
ANOMALY_THRESHOLD = float(os.getenv('ANOMALY_THRESHOLD', 1))
SEQUENCE_LENGTH = int(os.getenv('SEQUENCE_LENGTH', 16))
FEATURE_COUNT = int(os.getenv('FEATURE_COUNT', 12))
 
thread_local = threading.local()
producer_manager, consumer_manager = initialize_kafka_managers(KAFKA_BROKER)
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s',
                    handlers=[logging.StreamHandler()])

logging.info(f"App directory contents: {os.listdir(APP_PATH)}")
logging.info(f"Configured Kafka broker URL: {KAFKA_BROKER}")
logging.info(f"Configured TorchServe requests URL: {TORCHSERVE_REQUESTS_URL}")
logging.info(f"Configured TorchServe management URL: {TORCHSERVE_MANAGEMENT_URL}")
logging.info(f"Configured capture interface: {CAPTURE_INTERFACE}")

def get_thread_name():
    if not hasattr(thread_local, 'thread_name'):
        thread_local.thread_name = threading.current_thread().name
    return thread_local.thread_name

##################################################
# DATA PROCESSING
##################################################

def process_raw_data():
    consumer = consumer_manager.get_consumer(RAW_TOPIC, 'network_data')
    processed_producer = producer_manager.get_producer(PROCESSED_TOPIC)
    training_producer = producer_manager.get_producer(TRAINING_TOPIC)
    
    while True:
        msg = consumer.poll(1.0)
        if msg is None:
            continue
        if msg.error():
            logging.error(f"Consumer error: {msg.error()}")
            continue

        try:
            value = json.loads(msg.value().decode('utf-8'))
            logging.debug(f"Received raw data message: {value}")

            if not all(k in value for k in ('_id', 'timestamp', 'sequence', 'is_training', 'human_readable', 'is_anomaly', 'is_false_positive', 'reconstruction_error')):
                logging.error(f"Missing keys in the message: {value}")
                continue

            _id = value['_id']
            is_training = value['is_training']
            sequence = value['sequence']
            human_readable_list = value['human_readable']

            if not isinstance(sequence, list) or len(sequence) != SEQUENCE_LENGTH:
                logging.error(f"Invalid sequence structure or length in message: {value}")
                continue
            if len(sequence) != len(human_readable_list):
                logging.error(f"Mismatch between sequence length and human_readable length in message: {value}")
                continue

            processed_value = {
                "_id": _id,  # Include the _id from the raw data
                "timestamp": datetime.now(),
                "sequence": sequence,
                "is_training": is_training,
                "human_readable": human_readable_list,
                "is_anomaly": False,  # Default value, will be updated later
                "reconstruction_error": None,  # Will be updated by the model
                "is_false_positive": None  # This field is not in the schema, consider removing or adding to schema
            }

            logging.debug(f"Producing processed data: {processed_value}")
            processed_producer.produce(
                topic=PROCESSED_TOPIC,
                key=str(_id),
                value=json.dumps(processed_value, cls=CustomEncoder),
            )

            if is_training:
                training_producer.produce(
                    topic=TRAINING_TOPIC,
                    key=str(_id),
                    value=json.dumps(processed_value, cls=CustomEncoder),
                )
            consumer.commit(msg)
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding message: {e}")
        except KeyError as ke:
            logging.error(f"KeyError: {ke} in message: {value}")
        except ValueError as ve:
            logging.error(f"ValueError: {ve}")
        except TypeError as te:
            logging.error(f"TypeError: {te}")
        except Exception as e:
            logging.error(f"Unexpected error processing data from raw topic: {e}")

##################################################
# MODEL
##################################################

model_ready_event = threading.Event()

def train_model_process():
    update_training_status("starting", 0, "Initiating model training process")

    try:
        update_training_status("checking_torchserve", 10, "Checking TorchServe availability")
        if not check_torchserve_availability():
            update_training_status("error", 0, "TorchServe is not available")
            return

        update_training_status("fetching_data", 20, "Fetching data from Kafka")
        data = fetch_training_data()
        update_training_status("data_fetched", 40, f"Fetched {len(data)} records from Kafka")
        time.sleep(5)
        update_training_status("training", 50, "Training model")
        if train_and_set_inference_mode(data):
            update_training_status("completed", 100, "Model training completed and set to inference mode")
            threading.Thread(target=prediction_thread).start()
        else:
            update_training_status("error", 0, "Failed to train model and set to inference mode")
    except Exception as e:
        logging.error(f"Error during model training process: {str(e)}")
        update_training_status("error", 0, f"Error during training: {str(e)}")

def check_torchserve_availability():
    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = requests.get(f"{TORCHSERVE_MANAGEMENT_URL}/models")
            if response.status_code == 200:
                logging.info("TorchServe is available")
                return True
        except requests.RequestException as e:
            logging.warning(f"TorchServe not available (attempt {attempt + 1}): {e}")
        time.sleep(5)
    logging.error("TorchServe is not available after multiple attempts")
    return False

def fetch_labeled_data():
    consumer = consumer_manager.get_consumer(LABELED_TOPIC, 'labeled_data_consumer_group')
    labeled_data = []
    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                logging.error(f"Consumer error: {msg.error()}")
                continue
            
            try:
                value = json.loads(msg.value().decode('utf-8'))
                labeled_data.append(value)
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding message: {e}")
            except Exception as e:
                logging.error(f"Unexpected error processing message: {e}")
    except Exception as e:
        logging.error(f"Error fetching labeled data: {e}")
    finally:
        consumer_manager.close_consumer(consumer)
    
    return labeled_data

def fetch_training_data():
    logging.info("Fetching all unread data from training topic")
    consumer_manager.consumer_config = consumer_manager.consumer_config.copy()
    consumer_manager.consumer_config['auto.offset.reset'] = 'earliest'
    consumer = consumer_manager.get_consumer(TRAINING_TOPIC, f'training_group_{int(time.time())}', config=consumer_manager.consumer_config)

    data = []
    max_empty_polls = 5
    empty_poll_count = 0

    try:
        while empty_poll_count < max_empty_polls:
            msg = consumer.poll(1.0)
            if msg is None:
                empty_poll_count += 1
                logging.info(f"Empty poll {empty_poll_count}/{max_empty_polls}")
                continue
            if msg.error():
                logging.error(f"Consumer error: {msg.error()}")
                continue

            try:
                value = json.loads(msg.value().decode('utf-8'))
                data.append(value)
                empty_poll_count = 0  # Reset empty poll count on successful message
                logging.debug(f"Received message: {value}")
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding message: {e}")
            except Exception as e:
                logging.error(f"Unexpected error processing message fetching training data: {e}")

    finally:
        consumer_manager.close_consumer(consumer)
    
    logging.info(f"Fetched data of size {len(data)} from {TRAINING_TOPIC}")
    return data

def train_and_set_inference_mode(data, is_labeled=False):
    if not check_torchserve_availability():
        logging.error("Cannot proceed as TorchServe is not available")
        return False

    if not train_model(data, is_labeled):
        logging.error("Failed to train model")
        return False

    logging.info("Attempting to set model to inference mode")
    max_retries = 30
    for attempt in range(max_retries):
        try:
            url = f"{TORCHSERVE_MANAGEMENT_URL}/models/{MODEL_NAME}"
            params = {'min_worker': '1'}
            

            response = requests.put(url, params=params)
            response.raise_for_status()
            logging.info(f"PUT request successful, status code: {response.status_code}")
            
            # Verify that the model is in inference mode
            if check_model_availability():
                logging.info("Model set to inference mode successfully")
                model_ready_event.set()
                return True
            else:
                logging.warning(f"Model not in READY state after setting to inference mode (attempt {attempt + 1})")
        except requests.exceptions.RequestException as e:
            logging.warning(f"Error setting model to inference mode (attempt {attempt + 1}): {e}")
        
        time.sleep(min(30, 2 ** attempt))  # Exponential backoff with a maximum of 30 seconds

    logging.error("Failed to set model to inference mode after all attempts")
    return False

def train_model(data, is_labeled=False):
    max_retries = 5

    logging.info("Attempting to train model.")
    
    # Check if the model is already registered
    try:
        response = requests.get(f"{TORCHSERVE_MANAGEMENT_URL}/models/{MODEL_NAME}")
        if response.status_code == 200:
            logging.info(f"Model {MODEL_NAME} is registered.")
    except requests.RequestException as e:
        logging.error(f"Error checking existing model registration: {e}")

    # Extract features from the data
    try:
        if is_labeled:
            sequences = [packet['sequence']['sequence'] for packet in data]
        else:
            sequences = [packet['sequence'] for packet in data]
        
        logging.info(f"Extracted {len(sequences)} sequences from data")
        logging.debug(f"First sequence: {sequences[0]}")
    except Exception as e:
        logging.error(f"Error extracting sequences from data: {e}")
        logging.debug(f"Data sample: {data[:5]}")  # Log a sample of the data
        return False
    
    for attempt in range(max_retries):
        try:
            url = f"{TORCHSERVE_REQUESTS_URL}/predictions/{MODEL_NAME}"
            headers = {'X-Request-Type': 'train'}
            
            payload = {
                'data': sequences,
                'is_labeled': is_labeled
            }
            payload_size = sys.getsizeof(json.dumps(payload))
            logging.info(f"Attempting to send payload of size: {payload_size} bytes")
            logging.info(f"Sending model training request with {len(sequences)} sequences (attempt {attempt + 1})")
            response = requests.post(url, json=payload, headers=headers)
            
            if response.status_code == 200:
                training_result = response.json()
                if training_result.get('status') == 'success':
                    logging.info("Model training completed successfully")
                    return True
                else:
                    logging.error(f"Model training failed: {training_result.get('message', 'Unknown error')}")
            else:
                logging.error(f"Unexpected response from TorchServe: {response.text}")
            
            return False
        except requests.RequestException as e:
            logging.error(f"Error sending model training request (attempt {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                logging.error(f"Failed to send model training request after {max_retries} attempts")
                return False
            time.sleep(min(30, 5 * (attempt + 1)))  # Linear backoff with a maximum of 30 seconds
    
    return False

def train_with_labeled_data():
    update_training_status("starting", 0, "Initiating model training with labeled data")

    try:
        update_training_status("checking_torchserve", 10, "Checking TorchServe availability")
        if not check_torchserve_availability():
            update_training_status("error", 0, "TorchServe is not available")
            return

        update_training_status("fetching_data", 20, "Fetching labeled data from Kafka")
        labeled_data = fetch_labeled_data()
        update_training_status("data_fetched", 40, f"Fetched {len(labeled_data)} labeled records from Kafka")
        logging.debug(f"Sample of fetched data: {labeled_data[:5]}")  # Log a sample of the fetched data

        update_training_status("training", 50, "Training model with labeled data")
        if train_and_set_inference_mode(labeled_data, is_labeled=True):
            update_training_status("completed", 100, "Model training with labeled data completed and set to inference mode")
            threading.Thread(target=prediction_thread).start()
        else:
            update_training_status("error", 0, "Failed to train model with labeled data and set to inference mode")
    except Exception as e:
        logging.error(f"Error during model training process with labeled data: {str(e)}", exc_info=True)
        update_training_status("error", 0, f"Error during training with labeled data: {str(e)}")


def prediction_thread():
    logging.info("Starting prediction thread")
    consumer = consumer_manager.get_consumer(PROCESSED_TOPIC, 'prediction_group')

    wait_for_model_ready()
    logging.info("Model is ready. Starting to process messages.")
    
    try:
        producer = producer_manager.get_producer(PREDICTIONS_TOPIC)
    except KafkaException as e:
        logging.error(f"Failed to get producer for {PREDICTIONS_TOPIC}: {e}")
        return

    try:
        while True:
            if not check_model_availability():
                logging.error("Model is not available. Waiting before retrying...")
                time.sleep(10)  # Wait for 10 seconds before retrying
                continue

            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                logging.error(f"Consumer error: {msg.error()}")
                continue

            try:
                value = json.loads(msg.value().decode('utf-8'))
                logging.info(f"Processing message: {value['_id']}")
                
                # Check for required fields
                required_fields = ['_id', 'sequence', 'timestamp', 'is_training']
                if not all(field in value for field in required_fields):
                    logging.warning(f"Received message missing required fields: {value}")
                    continue
                
                _id = value['_id']
                sequence = value['sequence']
                
                if not isinstance(sequence, list) or len(sequence) != SEQUENCE_LENGTH:
                    logging.warning(f"Invalid sequence structure or length for {_id}. Expected list of length {SEQUENCE_LENGTH}, got {type(sequence)} of length {len(sequence)}")
                    continue
                
                prediction = query_model(sequence)
                
                if "error" in prediction:
                    logging.error(f"Error in prediction for {_id}: {prediction['error']}")
                    continue
                
                anomaly_results = prediction.get('anomaly_results', [])
                if not anomaly_results:
                    logging.warning(f"No anomaly results in prediction for {_id}")
                    continue

                for result in anomaly_results:
                    reconstruction_error = float(result['reconstruction_error'])
                    is_anomaly = reconstruction_error >= float(ANOMALY_THRESHOLD)
                    output = {
                        "_id": _id,  # Include the _id in the output
                        "timestamp": datetime.now(),  # Use current UTC time
                        "sequence": value['sequence'],
                        "human_readable": value.get('human_readable', []),
                        "is_anomaly": is_anomaly,
                        "is_training": value['is_training'],
                        "reconstruction_error": reconstruction_error,
                        "is_false_positive": None  # This field is not in the schema, consider removing or adding to schema
                    }
                    logging.debug(f'Producing prediction for sequence {_id}: is_anomaly = {is_anomaly}, reconstruction_error = {reconstruction_error}')
                    
                    logging.debug(f"Producing processed data to topic '{PREDICTIONS_TOPIC}':")
                    logging.debug(f"Key: {_id}")
                    logging.debug(f"Value: {json.dumps(output, indent=2, cls=CustomEncoder)}")

                    producer.produce(
                        PREDICTIONS_TOPIC, 
                        key=str(_id), 
                        value=json.dumps(output, cls=CustomEncoder),
                        on_delivery=delivery_report
                    )                
                producer.flush()
                logging.debug(f"Produced prediction for sequence {_id}")
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding message: {e}")
            except Exception as e:
                logging.error(f"Unexpected error processing message in prediction thread: {e}", exc_info=True)

    except KeyboardInterrupt:
        logging.info("Prediction thread interrupted.")
    finally:
        consumer.close()
        logging.info("Prediction thread ended.")

def delivery_report(err, msg):
    if err is not None:
        logging.error(f'Message delivery failed: {err}')
    else:
        logging.debug(f'Message delivered to {msg.topic()} [{msg.partition()}]')

def wait_for_model_ready():
    logging.info("Waiting for model to be ready...")
    model_ready_event.wait()  # Block until the event is set
    logging.info("Model is ready")

def query_model(data):
    url = f"{TORCHSERVE_REQUESTS_URL}/predictions/{MODEL_NAME}"
    
    logging.debug(f"Querying model with data: {data}")
    try:
        response = requests.post(url, json=data)
        response.raise_for_status()
        prediction = response.json()
        logging.debug(f"Received prediction {prediction} using weights from: {prediction.get('weights_file')}")
        return prediction
    except requests.exceptions.RequestException as e:
        logging.error(f"Error querying model: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logging.error(f"Response status code: {e.response.status_code}")
            logging.error(f"Response content: {e.response.text}")
        return {"error": f"Failed to get prediction: {str(e)}"}

def check_model_availability():
    try:
        url = f"{TORCHSERVE_MANAGEMENT_URL}/models/{MODEL_NAME}"
        response = requests.get(url)
        response.raise_for_status()
        model_status = response.json()
        
        if not model_status or not isinstance(model_status, list) or len(model_status) == 0:
            logging.warning("No models found in status response")
            return False
        
        model_info = model_status[0]
        if 'workers' not in model_info or not model_info['workers']:
            logging.warning("No workers found for the model")
            return False
        
        worker_status = model_info['workers'][0]['status']
        if worker_status != 'READY':
            logging.warning(f"Model worker is not in READY state. Current state: {worker_status}")
            return False
        
        logging.info("Model is available and ready")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Error checking model availability: {e}")
        return False
    except (KeyError, IndexError, TypeError) as e:
        logging.error(f"Error parsing model status response: {e}")
        return False

##################################################
# FLASK FOR MODEL TRAINING - thread 4
##################################################

model_training = False
app = Flask(__name__)

@app.route('/train_data', methods=['POST'])
def train_data():
    data = request.json
    if 'file_path' in data:
        # Process PCAP file
        file_path = os.path.join(TRAINING_DATA_PATH, data['file_path'])  # Use TRAINING_DATA_PATH
        read_pcap(file_path, KAFKA_BROKER)
    elif 'startDate' in data and 'endDate' in data:
        # Process data within time window
        start_date = datetime.fromtimestamp(int(data['startDate']) / 1000, timezone.utc)
        end_date = datetime.fromtimestamp(int(data['endDate']) / 1000, timezone.utc)
        process_time_window(start_date, end_date)
    else:
        return jsonify({"error": "Invalid training data provided"}), 400

    return jsonify({"message": "Training data uploaded"}), 202

@app.route('/train_with_labeled_data', methods=['POST'])
def start_train_with_labeled_data():
    threading.Thread(target=train_with_labeled_data).start()
    return jsonify({"message": "Training with labeled data initiated"}), 200


@app.route('/training_start', methods=['POST'])
def start_training_job():
    threading.Thread(target=train_model_process).start()
    return jsonify({"message": "Training job started"}), 200

@app.route('/status', methods=['GET'])
def get_status():
    return jsonify(get_training_status())

@app.route('/health', methods=['GET'])
def health_check():
    # Implement health check logic
    return jsonify({"status": "healthy"}), 200

##################################################
# MAIN
##################################################

def main():
    logging.info("Cove Security data processing engine starting")
    time.sleep(5)

    available_interfaces = get_available_interfaces()
    logging.info(f"Available network interfaces: {available_interfaces}")

    if CAPTURE_INTERFACE not in available_interfaces:
        logging.error(f"Specified interface {CAPTURE_INTERFACE} not found. Please choose from: {available_interfaces}")
        return
    logging.info(CAPTURE_INTERFACE)
    threads = [
        threading.Thread(name='TrafficCaptureThread', target=capture_live_traffic, args=(CAPTURE_INTERFACE,)),
        threading.Thread(name='DataProcessingThread', target=process_raw_data),
        threading.Thread(name='PredictionThread', target=prediction_thread),
        threading.Thread(name='AppThread', target=app.run, kwargs={'host': '0.0.0.0', 'port': FLASK_PORT})
    ]

    for thread in threads:
        thread.start()

    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        logging.info("Shutting down ...")

if __name__ == "__main__":
    main()