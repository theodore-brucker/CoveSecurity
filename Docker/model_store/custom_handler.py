import json
import logging
import os
import time
import numpy as np
import torch
from torch.utils.data import DataLoader, TensorDataset
from ts.torch_handler.base_handler import BaseHandler
from torch.optim.lr_scheduler import ExponentialLR
from transformer_autoencoder import TransformerAutoencoder

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
ANOMALY_THRESHOLD = float(os.getenv('ANOMALY_THRESHOLD', 1))
SEQUENCE_LENGTH = int(os.getenv('SEQUENCE_LENGTH', 16))
FEATURE_COUNT = int(os.getenv('FEATURE_COUNT', 12))
MODEL_STORE_PATH = os.getenv('MODEL_STORE_PATH', '/home/model-server/model-store/')

class SequenceAnomalyDetector(BaseHandler):
    def __init__(self):
        super().__init__()
        self.model = None
        self.device = None
        self.initialized = False
        self.sequence_length = SEQUENCE_LENGTH
        self.feature_dim = FEATURE_COUNT
        self.model_dir = None
        self.weights_file = None
        self.anomaly_threshold = ANOMALY_THRESHOLD
        self.model_store_path = MODEL_STORE_PATH
        self.model_checkpoints_path = os.path.join(self.model_store_path, 'checkpoints/')

    def initialize(self, context):
        logger.info("Initializing SequenceAnomalyDetector")

        logger.debug(f'Contents of {self.model_store_path}:')
        for item in os.listdir(self.model_store_path):
            item_path = os.path.join(self.model_store_path, item)
            if os.path.isfile(item_path):
                logger.debug(f"  File: {item} - Size: {os.path.getsize(item_path)} bytes")
            elif os.path.isdir(item_path):
                logger.debug(f"  Directory: {item}")

        self.manifest = context.manifest
        self.model_dir = context.system_properties.get("model_dir")
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        if not self.model_dir:
            raise ValueError("model_dir is not set in system properties")

        self.model = TransformerAutoencoder(input_size=self.feature_dim, sequence_length=self.sequence_length).to(self.device)
        logger.info(f"Model created and moved to device: {self.device}")

        self.load_model_version('latest')
        self.initialized = True
        logger.info("SequencecAnomalyDetector initialization completed")

    def load_latest_weights(self):
        if not self.model_dir:
            logger.error("model_dir is not set")
            return

        pth_files = [f for f in os.listdir(self.model_store_dir) if f.endswith('.pth')]
        if not pth_files:
            logger.warning("No .pth files found in the model directory")
            self.weights_file = None
            return

        latest_file = max(pth_files, key=lambda f: os.path.getmtime(os.path.join(self.model_store_dir, f)))
        self.weights_file = os.path.join(self.model_store_dir, latest_file)
        logger.info(f"Loading weights from: {self.weights_file}")

        try:
            state_dict = torch.load(self.weights_file, map_location=self.device)
            self.model.load_state_dict(state_dict)
            logger.info(f"Successfully loaded weights from {self.weights_file}")
        except Exception as e:
            logger.error(f"Error loading weights: {str(e)}")
            self.weights_file = None

    def inference(self, dataloader):
        logger.debug("Starting inference")
        self.model.eval()
        results = []
        with torch.no_grad():
            for batch in dataloader:
                batch = batch[0].to(self.device)
                output, _ = self.model(batch)
                anomaly_scores, _ = self.model.compute_anomaly_score(batch)
                results.extend(zip(batch.cpu().numpy(), output.cpu().numpy(), anomaly_scores.cpu().numpy()))
        logger.debug(f"Inference completed. Total sequences processed: {len(results)}")
        return results

    def preprocess(self, data):
        logger.debug("Starting preprocessing")
        logger.debug(f'Data type before processing: {type(data)}, Content: {data}')
        
        # Check if the data is in the training format (list of dicts with 'body')
        if isinstance(data, list) and isinstance(data[0], dict) and 'body' in data[0]:
            # Training data format
            sequences = data[0]['body']
            logger.debug("Data is a dictionary with a body field, likely for training")
        # Check if the data is a list of sequences (inference with one sequence wrapped in a list)
        elif isinstance(data, list) and isinstance(data[0], list) and len(data) > 0:
            sequences = data  # Treat it as a single sequence wrapped in a list
            logger.debug("Data is a list with a sequence, likely for inference")
        
        else:
            # Invalid data format
            logger.error("Invalid input data format")
            raise ValueError("Input data must be a dictionary with a body key")

        logging.debug(sequences)
        # Convert data to tensor
        tensor_data = torch.tensor(sequences, dtype=torch.float32)
        
        if tensor_data.ndimension() == 2:  # Single sequence case
            tensor_data = tensor_data.unsqueeze(0)  # Add batch dimension
        
        if tensor_data.shape[1:] != (self.sequence_length, self.feature_dim):
            logger.error(f"Invalid input shape: {tensor_data.shape}")
            raise ValueError(f"Each sequence must have shape ({self.sequence_length}, {self.feature_dim})")

        dataset = TensorDataset(tensor_data)
        batch_size = 1 if len(dataset) == 1 else 32
        dataloader = DataLoader(dataset, batch_size, shuffle=False)
        
        logger.info(f"Preprocessing completed. DataSet size: {len(dataset)}, DataLoader batches: {len(dataloader)}")
        return dataloader

    def postprocess(self, inference_outputs):
        logger.info("Starting postprocessing")
        anomaly_results = []

        for i, (original, reconstructed, anomaly_score) in enumerate(inference_outputs):
            anomaly_results.append({
                "sequence_id": i,
                "reconstruction_error": float(anomaly_score),
                "is_anomaly": bool(float(anomaly_score) >= float(self.anomaly_threshold))
            })

        logger.info(f"Postprocessing completed. Processed {len(anomaly_results)} sequences")
        return anomaly_results

    def handle(self, data, context):
        logger.debug("Handling new request")
        if not self.initialized:
            self.initialize(context)
        try:
            if context.get_request_header(0, "X-Request-Type") == "train":
                logger.info("Received training request")
                response = self.train(data, context)
                logger.info("Training request handled successfully")
                return response
            else:
                logger.debug("Received inference request")

                model_version = context.get_request_header(0, "X-Model-Version")
                if model_version:
                    self.load_model_version(model_version)

                dataloader = self.preprocess(data)
                inference_outputs = self.inference(dataloader)
                anomaly_results = self.postprocess(inference_outputs)
                response = {
                    "anomaly_results": anomaly_results,
                    "weights_file": self.weights_file or "No weights file loaded"
                }
                logger.debug("Inference request handled successfully")
                return [json.dumps(response)]
        except Exception as e:
            logger.error(f"Error handling request: {str(e)}", exc_info=True)
            return [json.dumps({"status": "error", "message": str(e)})]

    def set_threshold(self, train_dataloader, percentile=90):
        self.model.eval()
        reconstruction_errors = []

        with torch.no_grad():
            for inputs in train_dataloader:
                inputs = inputs[0].to(self.device)
                outputs, _ = self.model(inputs)
                recon_error = self.model.compute_batch_error((outputs, None), inputs)
                reconstruction_errors.extend(recon_error.cpu().numpy())

        threshold = np.percentile(reconstruction_errors, percentile)
        logger.info(f"Threshold set at the {percentile}th percentile: {threshold}")
        return threshold

    def train(self, data, context):
        logger.info("Starting model training/fine-tuning")

        num_epochs = int(os.getenv('NUM_EPOCHS', 10))
        early_stopping_threshold = float(os.getenv('EARLY_STOPPING_THRESHOLD', 0.01))
        
        # Load the latest checkpoint if it exists
        latest_checkpoint = self.get_latest_checkpoint()
        if latest_checkpoint:
            logger.info("Loading latest checkpoint for fine-tuning")
            self.load_checkpoint(latest_checkpoint)
        else:
            logger.info("No checkpoint found. Starting initial training.")

        self.model.train()
        optimizer = torch.optim.Adam(self.model.parameters(), lr=0.001)
        
        # Implement a simple learning rate scheduler
        scheduler = ExponentialLR(optimizer, gamma=0.95)
        
        dataloader = self.preprocess(data)

        for epoch in range(1, num_epochs + 1):
            logger.info(f"Epoch {epoch} started")
            epoch_loss = 0
            num_batches = 0

            for batch in dataloader:
                batch = batch[0].to(self.device)
                optimizer.zero_grad()
                outputs, _ = self.model(batch)
                loss = self.model.compute_loss((outputs, None), batch)

                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                optimizer.step()
                
                epoch_loss += loss.item()
                num_batches += 1
                
                logger.debug(f"Epoch {epoch}, Batch {num_batches}, Loss: {loss.item()}")

            average_epoch_loss = epoch_loss / num_batches if num_batches > 0 else 0
            logger.info(f"Epoch {epoch} completed. Average loss: {average_epoch_loss}")
            
            # Apply learning rate scheduler
            scheduler.step()

            # Early stopping check
            if average_epoch_loss < early_stopping_threshold:
                logger.info(f"Early stopping triggered at epoch {epoch} with average loss {average_epoch_loss}")
                break

        # After training, set a dynamic threshold based on the training data
        self.anomaly_threshold = self.set_threshold(dataloader)
        logger.info(f"Dynamic anomaly threshold set to: {self.anomaly_threshold}")

        # Save the model using the save_checkpoint function
        model_file_name = self.save_checkpoint(
            epoch=num_epochs,
            model_state=self.model.state_dict(),
            optimizer_state=optimizer.state_dict(),
            loss=average_epoch_loss,
            metrics={'anomaly_threshold': self.anomaly_threshold}
        )

        # Update the model registry
        self.update_model_registry(model_file_name, average_epoch_loss)

        return [json.dumps({"status": "success", "average_loss": average_epoch_loss, "model_file": model_file_name})]

    def load_model_version(self, version):
        logger.info(f"Loading model version: {version}")
        
        # Read the model registry
        registry_path = '/home/model-server/model-registry/model_registry.json'
        logger.debug(f"Reading model registry from: {registry_path}")
        
        try:
            with open(registry_path, 'r') as f:
                model_registry = json.load(f)
                logger.debug(f"Model registry content: {model_registry}")
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Error loading model registry: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to load model registry: {e}")
            raise

        if version == 'latest':
            version = model_registry.get('latest', None)
            if version is None:
                logger.warning("No 'latest' version found in the model registry. Initializing model with random weights.")
                return

        # Get the checkpoint path for the requested version
        checkpoint_path = model_registry.get(version, {}).get('checkpoint_path', None)
        
        if checkpoint_path is None or not os.path.isfile(checkpoint_path):
            logger.warning(f"Checkpoint file not found for version {version}. Initializing model with random weights.")
            return
        
        # Load the checkpoint
        try:
            checkpoint = torch.load(checkpoint_path, map_location=self.device)
            
            # Load the state dictionary
            if 'model_state_dict' in checkpoint:
                self.model.load_state_dict(checkpoint['model_state_dict'])
            else:
                self.model.load_state_dict(checkpoint)  # Assuming the checkpoint is the state dict itself

            logger.info(f"Successfully loaded model version: {version}")
        except Exception as e:
            logger.error(f"Error loading checkpoint for version {version}: {e}")
            logger.warning("Initializing model with random weights.")

    def save_checkpoint(self, epoch, model_state, optimizer_state, loss, metrics):
        timestamp = f"{time.time():.2f}".replace('.', '_')
        model_file_name = f'transformer_autoencoder_{timestamp}.pth'
        model_save_path = os.path.join(self.model_checkpoints_path, model_file_name)
        
        checkpoint = {
            'epoch': epoch,
            'model_state_dict': model_state,
            'optimizer_state_dict': optimizer_state,
            'loss': loss,
            'metrics': metrics
        }
        
        torch.save(checkpoint, model_save_path)
        logger.info(f"Checkpoint saved to {model_save_path}")
        return model_file_name

    def get_latest_checkpoint(self):
        logger.info('Getting the latest checkpoint from the checkpoints folder')
        checkpoints = [f for f in os.listdir(self.model_checkpoints_path) if f.endswith('.pth')]
        if not checkpoints:
            logger.info('No checkpoints found')
            return None
        latest_checkpoint = max(checkpoints, key=lambda x: os.path.getctime(os.path.join(self.model_checkpoints_path, x)))
        return os.path.join(self.model_checkpoints_path, latest_checkpoint)

    def load_checkpoint(self, checkpoint_path):
        logger.info(f'Loading checkpoint from {checkpoint_path}')
        checkpoint = torch.load(checkpoint_path, map_location=self.device)
        self.model.load_state_dict(checkpoint['model_state_dict'])
        logger.info('Checkpoint loaded successfully')

    def update_model_registry(self, model_file_name, average_loss):
        registry_path = '/home/model-server/model-registry/model_registry.json'
        
        # Load the current registry
        with open(registry_path, 'r') as f:
            model_registry = json.load(f)
        
        # Create a new version entry
        new_version = f"v{len(model_registry)}"
        model_registry[new_version] = {
            "checkpoint_path": os.path.join(self.model_checkpoints_path, model_file_name),
            "performance_metrics": {
                "training_loss": average_loss
            },
            "creation_date": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        }
        
        # Update the latest version
        model_registry['latest'] = new_version
        
        # Save the updated registry
        with open(registry_path, 'w') as f:
            json.dump(model_registry, f, indent=4)
        
        logger.info(f"Model registry updated with new version: {new_version}")

    
    ####################
    # TO BE ADDED LATER
    ####################

    def get_validation_data(self):
        logging.info('getting validation data')
        time.sleep(1)
        logging.info('got validation data')
        return
    
    def rollback_to_best_checkpoint(self):
        logging.info('rolling back to best checkpoint')
        time.sleep(1)
        logging.info('rolled back to best checkpoint')
        return

    def evaluate_model(self, validation_data):
        logging.info('evaluating model on validation set')
        time.sleep(1)
        logging.info('evaluated model on validation set')
        return 0.2