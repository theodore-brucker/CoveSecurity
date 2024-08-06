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
        self.manifest = context.manifest
        self.model_dir = context.system_properties.get("model_dir")
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        if not self.model_dir:
            raise ValueError("model_dir is not set in system properties")

        self.model = TransformerAutoencoder(input_size=self.feature_dim, sequence_length=self.sequence_length).to(self.device)
        logger.info(f"Model created and moved to device: {self.device}")
        
        self.load_latest_weights()
        self.initialized = True
        logger.info("PacketAnomalyDetector initialization completed")

    def preprocess(self, data):
        logger.debug("Starting preprocessing")

        if isinstance(data, list) and isinstance(data[0], dict) and 'body' in data[0]:
            # Training data format
            data = data[0]['body']
        
        if not isinstance(data, list):
            logger.error("Invalid input data format")
            raise ValueError("Input data must be a list")

        # Convert data to tensor
        tensor_data = torch.tensor(data, dtype=torch.float32)
        
        # Reshape if necessary
        if len(tensor_data.shape) == 2:  # Single sequence
            tensor_data = tensor_data.unsqueeze(0)
        
        if tensor_data.shape[1:] != (self.sequence_length, self.feature_dim):
            logger.error(f"Invalid input shape: {tensor_data.shape}")
            raise ValueError(f"Each sequence must have shape ({self.sequence_length}, {self.feature_dim})")

        dataset = TensorDataset(tensor_data)
        dataloader = DataLoader(dataset, batch_size=32, shuffle=False)
        
        logger.info(f"Preprocessing completed. DataSet size: {len(dataset)}, DataLoader batches: {len(dataloader)}")
        return dataloader

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

    def postprocess(self, inference_outputs):
        logger.info("Starting postprocessing")
        anomaly_results = []

        for i, (original, reconstructed, anomaly_score) in enumerate(inference_outputs):
            anomaly_results.append({
                "sequence_id": i,
                "anomaly_score": float(anomaly_score),
                "reconstruction_error": float(((original - reconstructed) ** 2).mean())
            })

        logger.info(f"Postprocessing completed. Processed {len(anomaly_results)} sequences")
        return anomaly_results

    def handle(self, data, context):
        logger.debug("Handling new request")
        if not self.initialized:
            logger.info("Model not initialized. Initializing now.")
            self.initialize(context)

        try:
            if context.get_request_header(0, "X-Request-Type") == "train":
                logger.info("Received training request")
                response = self.train(data, context)
                logger.info(f"Training request handled successfully")
                return response
            else:
                logger.debug("Received inference request")
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
        
        self.model.train()
        optimizer = torch.optim.Adam(self.model.parameters(), lr=0.001)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', factor=0.5, patience=5, verbose=True)
        
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
                
                logger.info(f"Epoch {epoch}, Batch {num_batches}, Loss: {loss.item()}")

            average_epoch_loss = epoch_loss / num_batches if num_batches > 0 else 0
            logger.info(f"Epoch {epoch} completed. Average loss: {average_epoch_loss}")
            scheduler.step(average_epoch_loss)

            # Early stopping check
            if average_epoch_loss < early_stopping_threshold:
                logger.info(f"Early stopping triggered at epoch {epoch} with average loss {average_epoch_loss}")
                break

        # Save the model
        model_file_name = f'transformer_autoencoder_{int(time.time())}.pth'
        model_save_path = os.path.join('/home/model-server/model-store', model_file_name)
        torch.save(self.model.state_dict(), model_save_path)
        logger.info(f"Model saved to {model_save_path}")

        return [json.dumps({"status": "success", "average_loss": average_epoch_loss, "model_file": model_file_name})]