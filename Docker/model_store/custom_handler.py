import json
import logging
import os
import time
import torch
from torch.utils.data import DataLoader
from ts.torch_handler.base_handler import BaseHandler
from memory_autoencoder import MEMAE
from utils import PacketBuffer, PacketSequenceDataset

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class PacketAnomalyDetector(BaseHandler):
    def __init__(self):
        super().__init__()
        self.model = None
        self.device = None
        self.initialized = False
        self.packet_buffer = None
        self.sequence_length = 32
        self.feature_dim = 6
        self.model_dir = None
        self.weights_file = None
        logger.info("PacketAnomalyDetector initialized")

    def initialize(self, context):
        logger.info("Initializing PacketAnomalyDetector")
        self.manifest = context.manifest
        self.model_dir = context.system_properties.get("model_dir")
        self.device = torch.device("cpu")
        if not self.model_dir:
            raise ValueError("model_dir is not set in system properties")

        self.model = MEMAE().to(self.device)
        logger.info(f"Model created and moved to device: {self.device}")
        
        self.packet_buffer = PacketBuffer(max_size=32, feature_dim=self.feature_dim)
        logger.info(f"Packet buffer created with max_size=32 and feature_dim={self.feature_dim}")
        
        # Load the latest model weights if available
        #self.load_latest_model(model_dir)
        logger.info("Initialized a new model with random weights")
        self.initialized = True
        logger.info("PacketAnomalyDetector initialization completed")

    @staticmethod
    def pad_collate(batch):
        # batch is a list of numpy arrays, each with shape [seq_len, 6]
        sequences = batch
        lengths = torch.tensor([seq.shape[0] for seq in sequences])
        
        # Pad sequences to length 32
        padded_seqs = torch.zeros(len(sequences), 32, 6)
        for i, seq in enumerate(sequences):
            end = min(seq.shape[0], 32)
            padded_seqs[i, :end, :] = seq[:end, :]
        
        return padded_seqs, lengths

    def preprocess(self, data):
        logger.debug("Starting preprocessing")

        if isinstance(data, list) and isinstance(data[0], dict) and 'body' in data[0]:
            # Training data format
            data = data[0]['body']
        
        if not isinstance(data, list):
            logger.error("Invalid input data format")
            raise ValueError("Input data must be a list")

        # Handle both single packet and multiple packet inputs
        if isinstance(data[0], (int, float)):
            data = [data]  # Single packet, wrap in list

        for packet in data:
            if len(packet) != self.feature_dim:
                logger.error(f"Packet data {packet}")
                logger.error(f"Invalid packet: expected {self.feature_dim} features, got {len(packet)}")
                raise ValueError(f"Each packet must have exactly {self.feature_dim} features")
            self.packet_buffer.add_packet(packet)

        logger.debug(f"Packet buffer size after processing: {len(self.packet_buffer)}")

        if len(self.packet_buffer) < self.sequence_length:
            logger.debug(f"Not enough packets in buffer. Current size: {len(self.packet_buffer)}, Required: {self.sequence_length}")
            return None

        dataset = PacketSequenceDataset(self.packet_buffer, self.sequence_length, self.feature_dim)
        dataloader = DataLoader(dataset, batch_size=4, shuffle=False, collate_fn=self.pad_collate)
        
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
        self.load_latest_weights()
        logger.debug("Starting inference with weights: %s", self.weights_file)
        self.model.eval()
        results = []
        with torch.no_grad():
            for batch_tuple in dataloader:
                batch, lengths = batch_tuple  # Unpack the tuple
                logger.debug(f"Input batch shape: {batch.shape}")
                batch = batch.to(self.device)
                output, att = self.model(batch)
                logger.debug(f"Model output shape: {output.shape}")
                # Unpack batch into individual packets
                for i in range(batch.shape[0]):
                    results.append((batch[i], output[i]))
        logger.debug(f"Inference completed. Total packets processed: {len(results)}")
        return results

    def postprocess(self, inference_outputs):
        logger.info("Starting postprocessing")
        anomaly_results = []

        for i, (original, reconstructed) in enumerate(inference_outputs):
            logger.debug(f"Processing packet {i}. Original shape: {original.shape}, Reconstructed shape: {reconstructed.shape}")
            reconstruction_error = torch.nn.functional.mse_loss(reconstructed, original, reduction='mean').item()
            anomaly_results.append({
                "packet_id": i,
                "reconstruction_error": reconstruction_error
            })

        logger.info(f"Postprocessing completed. Processed {len(anomaly_results)} packets")
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
                self.weights_file = "/home/model-server/model-store/memory_autoencoder_latest.pth"
                logger.debug(f"Responding to request with: {response}")
                logger.info(f"Training request handled successfully")
                return response
            else:
                logger.debug("Received inference request")
                dataloader = self.preprocess(data)  # Call self.preprocess here
                if dataloader is None:
                    return [json.dumps({"status": "error", "message": "Not enough data for inference"})]
                
                inference_outputs = self.inference(dataloader)
                anomaly_results = self.postprocess(inference_outputs)
                logger.info(f"Anomaly results: {anomaly_results}")
                response = {
                    "anomaly_results": anomaly_results,
                    "weights_file": self.weights_file or "No weights file loaded"
                }
                
                logger.debug(f"Responding to inference with: {response}")
                logger.debug("Inference request handled successfully")
                self.packet_buffer.clear()
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
        
        dataloader = self.preprocess(data)
        if dataloader is None:
            return [json.dumps({"status": "error", "message": "Not enough data for training"})]

        total_loss = 0
        num_batches = 0

        for epoch in range(1, num_epochs + 1):
            logger.info(f"Epoch {epoch} started")
            epoch_loss = 0

            for batch_idx, (batch, lengths) in enumerate(dataloader):
                logger.debug(f"Training - Batch datatype: {type(batch)}")
                logger.debug(f"Training - Batch size: {batch.shape}")
                
                if batch.size(0) < 2:  # Skip batches with size less than 2
                    continue

                optimizer.zero_grad()
                
                # Move batch to the correct device
                batch = batch.to(self.device)
                
                outputs, att = self.model(batch)
                loss = self.model.compute_loss((outputs, att), batch)
                
                loss.backward()
                optimizer.step()
                
                total_loss += loss.item()
                epoch_loss += loss.item()
                num_batches += 1
                
                logger.info(f"Epoch {epoch}, Batch {batch_idx+1}, Loss: {loss.item()}")

            
            average_epoch_loss = epoch_loss / num_batches if num_batches > 0 else 0
            logger.info(f"Epoch {epoch} completed. Average loss: {average_epoch_loss}")

            # Early stopping check
            if average_epoch_loss < early_stopping_threshold:
                logger.info(f"Early stopping triggered at epoch {epoch} with average loss {average_epoch_loss}")
                break

        average_loss = total_loss / num_batches if num_batches > 0 else 0
        logger.info(f"Training completed. Average loss: {average_loss}")

        # Save the model
        model_file_name = f'memory_autoencoder_{int(time.time())}.pth'
        model_save_path = os.path.join('/home/model-server/model-store', model_file_name)
        torch.save(self.model.state_dict(), model_save_path)
        logger.info(f"Model saved to {model_save_path}")

        return [json.dumps({"status": "success", "average_loss": average_loss, "model_file": model_file_name})]

    def save_model(self):
        model_store_dir = "/home/model-server/model-store"
        model_path = os.path.join(model_store_dir, 'memory_autoencoder_latest.pth')
        torch.save(self.model.state_dict(), model_path)
        self.weights_file = model_path  # Update the weights_file attribute
        logger.info(f"Model saved to {model_path}")