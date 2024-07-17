import json
import torch
from torch import nn
from ts.torch_handler.base_handler import BaseHandler # type: ignore
from autoencoder import Autoencoder

class AutoencoderHandler(BaseHandler):
    def initialize(self, context):
        properties = context.system_properties
        model_dir = properties.get("model_dir")
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        
        # Load model
        model_pt_path = f"{model_dir}/autoencoder.pt"
        self.model = Autoencoder(input_dim=6)  # Replace 6 with actual input dimension
        self.model.load_state_dict(torch.load(model_pt_path, map_location=self.device))
        self.model.to(self.device)
        self.model.eval()
        
    def preprocess(self, data):
        packets = [item["body"] for item in data]
        packets = [json.loads(packet) for packet in packets]
        packets = torch.tensor(packets, dtype=torch.float32).to(self.device)
        return packets
    
    def inference(self, packets):
        with torch.no_grad():
            outputs = self.model(packets)
            loss = nn.functional.mse_loss(outputs, packets, reduction='none')
            loss = loss.mean(dim=1)
            anomalies = loss > 0.05  # Replace with your threshold
        return anomalies.cpu().numpy().tolist()
    
    def postprocess(self, anomalies):
        return anomalies