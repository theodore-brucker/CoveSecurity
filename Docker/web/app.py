from flask import Flask, render_template
from flask_socketio import SocketIO
from kafka import KafkaConsumer, errors
import json
import time
from prometheus_client import start_http_server, Summary, Counter

app = Flask(__name__)
socketio = SocketIO(app)

# Prometheus metrics
REQUEST_TIME = Summary('request_processing_seconds', 'Time spent processing request')
MESSAGES_CONSUMED = Counter('messages_consumed_total', 'Total number of messages consumed')

@app.route('/')
@REQUEST_TIME.time()
def index():
    return render_template('index.html')

def consume_classifications():
    while True:
        try:
            consumer = KafkaConsumer('raw_data', bootstrap_servers='kafka:9092')
            for message in consumer:
                data = json.loads(message.value.decode('utf-8'))
                socketio.emit('new_classification', data)
                MESSAGES_CONSUMED.inc()
            break  # Exit the loop if successful
        except errors.NoBrokersAvailable:
            print("No brokers available, retrying in 5 seconds...")
            time.sleep(5)

if __name__ == '__main__':
    # Start Prometheus metrics server
    start_http_server(8000)
    
    socketio.start_background_task(target=consume_classifications)
    socketio.run(app, host='0.0.0.0', port=5000)
