import h5py
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

def create_data_loaders(batch_dataset):
    try:
        data_loader = batch_dataset.prefetch(tf.data.experimental.AUTOTUNE)
        logging.info("Data loaders created successfully")
        return data_loader
    except Exception as e:
        logging.error("Error creating data loaders: %s", str(e))
        raise

def create_batches(data_tensor, batch_size):
    try:
        dataset = tf.data.Dataset.from_tensor_slices(data_tensor)
        dataset = dataset.batch(batch_size)
        logging.info("Batches created successfully with batch size %d", batch_size)
        return dataset
    except Exception as e:
        logging.error("Error creating batches: %s", str(e))
        raise

def verify_tensors_and_batches(data_tensor, batch_size):
    try:
        assert len(data_tensor.shape) == 2, "Data tensor should be 2-dimensional"
        dataset = create_batches(data_tensor, batch_size)
        for batch in dataset:
            assert batch.shape[0] <= batch_size, "Batch size should not exceed the specified batch_size"
            assert batch.shape[1] == data_tensor.shape[1], "Each batch should have the same number of features as the data tensor"
        logging.info("All checks passed!")
    except AssertionError as e:
        logging.error("Verification failed: %s", str(e))
        raise
    except Exception as e:
        logging.error("Error during verification: %s", str(e))
        raise