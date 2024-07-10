import h5py
import numpy as np
import tensorflow as tf
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_data_from_h5(file_path):
    try:
        with h5py.File(file_path, 'r') as f:
            data = f['packets'][:]
        data_tensor = tf.convert_to_tensor(data, dtype=tf.float32)
        logging.info("Data loaded successfully from %s", file_path)
        return data_tensor
    except Exception as e:
        logging.error("Error loading data from %s: %s", file_path, str(e))
        raise

def make_predictions(model, data):
    predictions = model.predict(data)
    mse = np.mean(np.square(data - predictions), axis=1)
    return mse
