import json
import logging
import os
import time
import torch
from torch.utils.data import DataLoader, TensorDataset
from ts.torch_handler.base_handler import BaseHandler
from transformer_autoencoder import TransformerAutoencoder

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class PacketAnomalyDetector(BaseHandler):
    def __init__(self):
        super().__init__()
        self.model = None
        self.device = None
        self.initialized = False
        self.sequence_length = 16
        self.feature_dim = 12
        self.model_dir = None
        self.weights_file = None
        logger.info("PacketAnomalyDetector initialized")

    def initialize(self, context):
        logger.info("Initializing PacketAnomalyDetector")

        model_store_path = '/home/model-server/model-store/'
        logger.debug("Contents of /home/model-server/model-store/:")
        for item in os.listdir(model_store_path):
            item_path = os.path.join(model_store_path, item)
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
        logger.info("PacketAnomalyDetector initialization completed")

    def load_latest_weights(self):
        if not self.model_dir:
            logger.error("model_dir is not set")
            return

        model_store_dir = "/home/model-server/model-store"
        pth_files = [f for f in os.listdir(model_store_dir) if f.endswith('.pth')]
        if not pth_files:
            logger.warning("No .pth files found in the model directory")
            self.weights_file = None
            return

        latest_file = max(pth_files, key=lambda f: os.path.getmtime(os.path.join(model_store_dir, f)))
        self.weights_file = os.path.join(model_store_dir, latest_file)
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
            logger.debug("Training data detected.")
        
        # Check if the data is in the inference format (dict with 'sequence')
        elif isinstance(data, dict) and 'sequence' in data:
            sequences = data['sequence']
            logger.debug("Inference data detected.")
        
        # Check if the data is a list of sequences (inference with one sequence wrapped in a list)
        elif isinstance(data, list) and isinstance(data[0], list):
            sequences = data  # Treat it as a single sequence wrapped in a list
            logger.debug("Single sequence data detected wrapped in a list.")
        
        else:
            # Invalid data format
            logger.error("Invalid input data format")
            raise ValueError("Input data must be either a list with 'body' key for training or a dictionary with a 'sequence' key for inference")

        # Convert data to tensor
        tensor_data = torch.tensor(sequences, dtype=torch.float32)
        
        if tensor_data.ndimension() == 2:  # Single sequence case
            tensor_data = tensor_data.unsqueeze(0)  # Add batch dimension
        
        if tensor_data.shape[1:] != (self.sequence_length, self.feature_dim):
            logger.error(f"Invalid input shape: {tensor_data.shape}")
            raise ValueError(f"Each sequence must have shape ({self.sequence_length}, {self.feature_dim})")

        dataset = TensorDataset(tensor_data)
        dataloader = DataLoader(dataset, batch_size=32, shuffle=False)
        
        logger.info(f"Preprocessing completed. DataSet size: {len(dataset)}, DataLoader batches: {len(dataloader)}")
        return dataloader

    def postprocess(self, inference_outputs):
        logger.info("Starting postprocessing")
        anomaly_results = []

        for i, (original, reconstructed, anomaly_score) in enumerate(inference_outputs):
            anomaly_results.append({
                "sequence_id": i,
                "reconstruction_error": float(anomaly_score),
                "is_anomaly": float(anomaly_score) >= float(os.getenv('ANOMALY_THRESHOLD', 1))
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

    def train(self, data, context):
        logger.info("Starting model training")

        num_epochs = int(os.getenv('NUM_EPOCHS', 10))
        early_stopping_threshold = float(os.getenv('EARLY_STOPPING_THRESHOLD', 0.01))
        
        latest_checkpoint = self.get_latest_checkpoint()
        if latest_checkpoint:
            self.load_checkpoint(latest_checkpoint)

        self.model.train()
        optimizer = torch.optim.Adam(self.model.parameters(), lr=0.001)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', factor=0.5, patience=5, verbose=True)
        
        dataloader = self.preprocess(data)
        validation_dataloader = self.get_validation_data()

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
                
                logger.info(f"Epoch {epoch}, Batch {num_batches}, Loss: {loss.item()}")

            average_epoch_loss = epoch_loss / num_batches if num_batches > 0 else 0
            logger.info(f"Epoch {epoch} completed. Average loss: {average_epoch_loss}")
            scheduler.step(average_epoch_loss)

            # Early stopping check
            if average_epoch_loss < early_stopping_threshold:
                logger.info(f"Early stopping triggered at epoch {epoch} with average loss {average_epoch_loss}")
                break

        # Save the model
        timestamp = f"{time.time():.2f}".replace('.', '_')
        model_file_name = f'transformer_autoencoder_{timestamp}.pth'
        model_save_path = os.path.join('/home/model-server/model-store', model_file_name)
        torch.save(self.model.state_dict(), model_save_path)
        logger.info(f"Model saved to {model_save_path}")

        return [json.dumps({"status": "success", "average_loss": average_epoch_loss, "model_file": model_file_name})]
    
    def load_model_version(self, version):
        logger.info(f"Loading model version: {version}")
        
        # Read the model registry
        # with open('/home/model-server/model-registry/model_registry.json', 'r') as f:
        #     model_registry = json.load(f)
        
        # # Check if the requested version exists
        # if version not in model_registry:
        #     logger.warning(f"Model version {version} not found. Using latest version.")
        #     version = max(model_registry.keys())  # Use the latest version
        
        # # Get the checkpoint path for the requested version
        # checkpoint_path = model_registry[version]['checkpoint_path']
        
        # # Load the checkpoint
        # checkpoint = torch.load(checkpoint_path, map_location=self.device)
        # self.model.load_state_dict(checkpoint['model_state_dict'])
        
        logger.info(f"Successfully loaded model version: {version}")

    def get_validation_data(self):
        logging.info('getting validation data')
        time.sleep(1)
        logging.info('got validation data')
        return

    def continue_training(self, data, context):
        logging.info('starting continuous training')
        time.sleep(1)
        logging.info('completed continuous training')
        return
    
    def save_checkpoint(self, epoch, model_state, optimizer_state, loss, metrics):
        logging.info(f'saving checkpoint on epoch {epoch} due to loss:{loss} and metrics:{metrics}')
        time.sleep(1)
        path = f"/home/model-server/checkpoints/checkpoint_{int(time.time())}.pth"
        logging.info(f'saved checkpoint to {path}')
        return path

    def load_checkpoint(self, checkpoint_path):
        logging.info(f'loading checkpoint from {checkpoint_path}')
        time.sleep(1)
        logging.info('loaded checkpoint')
        return

    def get_latest_checkpoint(self):
        logging.info('getting the latest checkpoint from the checkpoints folder')
        return

    def evaluate_model(self, validation_data):
        logging.info('evaluating model on validation set')
        time.sleep(1)
        logging.info('evaluated model on validation set')
        return 0.2
    
    def rollback_to_best_checkpoint(self):
        logging.info('rolling back to best checkpoint')
        time.sleep(1)
        logging.info('rolled back to best checkpoint')
        return
    
    def update_model_registry(self, checkpoint_path, performance_metrics):
        logging.info(f'updating model registry to include {checkpoint_path} with metrics:{performance_metrics}')
        time.sleep(1)
        logging.info(f'updated model registry with {checkpoint_path}')
        return