import torch
from ts.torch_handler.base_handler import BaseHandler
from autoencoder import Autoencoder

class CustomHandler(BaseHandler):
    def initialize(self, context):
        self.manifest = context.manifest
        properties = context.system_properties
        model_dir = properties.get("model_dir")
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # Initialize the model with the required input dimension
        input_dim = 6  # Your use case specifies an input dimension of 6
        self.model = Autoencoder(input_dim=input_dim)
        model_path = model_dir + "/autoencoder.pt"
        self.model.load_state_dict(torch.load(model_path, map_location=self.device))
        self.model.to(self.device)
        self.model.eval()

    def preprocess(self, data):
        # Convert the input data to tensor
        input_data = data[0].get("data") or data[0].get("body")
        input_tensor = torch.tensor(input_data, dtype=torch.float32).to(self.device)
        return input_tensor

    def inference(self, data):
        with torch.no_grad():
            output = self.model(data)
            if len(output.shape) == 1:  # Check if output is 1-dimensional
                reconstruction_error = torch.mean((output - data) ** 2)
            else:
                reconstruction_error = torch.mean((output - data) ** 2, dim=1)
        return reconstruction_error.cpu().numpy()

    def postprocess(self, data):
        # Handle scalar vs array data correctly
        if data.ndim == 0:  # Scalar case
            return [{"reconstruction_error": float(data)}]
        else:
            return [{"reconstruction_error": error} for error in data]
