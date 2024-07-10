import os
import tensorflow as tf
from tensorflow.keras.models import load_model # type: ignore
from tensorflow.keras.losses import MeanSquaredError # type: ignore
from tensorflow.keras.utils import get_custom_objects # type: ignore
import numpy as np
import logging
import sys

import yaml
# Define the path to the config.yaml file
config_path = os.path.abspath(os.path.join('..', 'configs', 'config.yaml'))

# Load the YAML file
with open(config_path, 'r') as file:
    config = yaml.safe_load(file)

sys.path.append(os.path.abspath(os.path.join('..', 'model/autoencoder/')))
from autoencoder import create_autoencoder

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

get_custom_objects().update({'MeanSquaredError': MeanSquaredError})

def train_autoencoder(data_loader, input_dim, epochs=5):
    save_path = config['model']['trained_model_path']
    
    try:
        logger.info("Creating the autoencoder model")
        autoencoder = create_autoencoder(input_dim)
        
        autoencoder.compile(optimizer='adam', loss=MeanSquaredError())
        
        for epoch in range(epochs):
            logger.info(f"Epoch {epoch+1}/{epochs}")
            for batch_data in data_loader:
                reshaped_data = tf.reshape(batch_data, (-1, input_dim))  # Use tf.reshape
                logger.info(f"Batch data shape: {reshaped_data.shape}")
                autoencoder.train_on_batch(reshaped_data, reshaped_data)
        
        autoencoder.save(save_path)
        logger.info(f"Model training complete and saved as {save_path}")
    except Exception as e:
        logger.error(f"Error during training: {e}")
        sys.exit(1)

def test_autoencoder(data_loader):
    save_path = config['model']['trained_model_path']
    
    try:
        logger.info("Loading the trained autoencoder model")
        # Load the trained model with custom_objects if necessary
        autoencoder = tf.keras.models.load_model(save_path, custom_objects={'MeanSquaredError': MeanSquaredError})

        # Compile the model with the correct loss function
        autoencoder.compile(optimizer='adam', loss=MeanSquaredError())

        total_mse = 0
        batch_count = 0

        for batch_data in data_loader:
            decoded_data = autoencoder.predict(batch_data)
            mse = np.mean(np.square(batch_data - decoded_data))
            total_mse += mse
            batch_count += 1

        average_mse = total_mse / batch_count if batch_count > 0 else 0
        logger.info(f"Average MSE: {average_mse}")

        return average_mse

    except Exception as e:
        logger.error(f"Error during testing: {e}")
        sys.exit(1)