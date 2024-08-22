import json
import tempfile
import pytest
import os
import glob
import time
import torch
from custom_handler import SequenceAnomalyDetector

class DummyModel(torch.nn.Module):
    def __init__(self, input_size):
        super().__init__()
        self.linear = torch.nn.Linear(input_size, input_size)
    
    def forward(self, x):
        return self.linear(x)

    def compute_loss(self, outputs, inputs):
        # Mean Squared Error loss to ensure we have a gradient for weight updates
        return torch.nn.functional.mse_loss(outputs, inputs)

class TestSequenceAnomalyDetector:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.detector = SequenceAnomalyDetector()
        self.detector.model = DummyModel(12)  # Use the custom DummyModel
        self.detector.optimizer = torch.optim.Adam(self.detector.model.parameters())
        self.detector.device = torch.device("cpu")
        
        # Create a temporary directory to simulate the model store and checkpoint directories
        self.temp_dir = tempfile.mkdtemp()
        self.detector.model_dir = self.temp_dir
        self.checkpoint_dir = os.path.join(self.temp_dir, "checkpoints")
        self.detector.model_registry_path = os.path.join(self.temp_dir, 'model_registry.json')  # Set model_registry_path
        os.makedirs(self.checkpoint_dir, exist_ok=True)
        
        # Update the model store and checkpoint paths in the detector instance
        self.detector.model_store_dir = os.path.join(self.temp_dir, "model-store")
        os.makedirs(self.detector.model_store_dir, exist_ok=True)

    def test_get_latest_checkpoint(self):
        # Create dummy checkpoint files in the temporary checkpoint directory
        open(os.path.join(self.checkpoint_dir, 'checkpoint_1000.pth'), 'w').close()
        time.sleep(0.1)
        open(os.path.join(self.checkpoint_dir, 'checkpoint_2000.pth'), 'w').close()
        
        # Ensure the detector looks for checkpoints in the correct temporary directory
        self.detector.model_dir = self.checkpoint_dir
        
        latest_checkpoint = self.detector.get_latest_checkpoint()
        assert latest_checkpoint.endswith('checkpoint_2000.pth')
        
        # Clean up created checkpoint files
        for file in glob.glob(os.path.join(self.checkpoint_dir, 'checkpoint_*.pth')):
            os.remove(file)

    def test_save_checkpoint(self):
        epoch = 10
        model_state = self.detector.model.state_dict()
        optimizer_state = self.detector.optimizer.state_dict()
        loss = 0.5
        metrics = {'accuracy': 0.85}
        
        checkpoint_path = self.detector.save_checkpoint(epoch, model_state, optimizer_state, loss, metrics)
        
        assert os.path.exists(checkpoint_path)
        
        # Verify contents
        checkpoint = torch.load(checkpoint_path)
        assert checkpoint['epoch'] == epoch
        assert checkpoint['loss'] == loss
        assert checkpoint['metrics'] == metrics
        
        # Clean up the checkpoint file
        os.remove(checkpoint_path)

    def test_load_checkpoint(self):
        # First, save a checkpoint in the temporary directory
        epoch = 10
        model_state = self.detector.model.state_dict()
        optimizer_state = self.detector.optimizer.state_dict()
        loss = 0.5
        metrics = {'accuracy': 0.85}
        
        checkpoint_path = self.detector.save_checkpoint(epoch, model_state, optimizer_state, loss, metrics)
        
        # Now, load the checkpoint
        success = self.detector.load_checkpoint(checkpoint_path)
        assert success
        
        # Verify the model state
        loaded_state_dict = self.detector.model.state_dict()
        for key in model_state.keys():
            assert torch.equal(model_state[key], loaded_state_dict[key])
        
        # Clean up the checkpoint file
        os.remove(checkpoint_path)

    def test_continue_training_with_existing_checkpoint(self):
        # Create a dummy initial checkpoint in the temporary directory
        initial_state_dict = self.detector.model.state_dict()
        initial_checkpoint_path = self.detector.save_checkpoint(0, initial_state_dict,
                                                                self.detector.optimizer.state_dict(),
                                                                1.0, {})

        # Create dummy training data
        # 10 sequences of 16 timesteps with 12 features; values are small but non-zero to ensure gradient
        dummy_data = [{'body': [[[0.1] * 12] * 16] * 10}]  

        # Run continuous training
        result = self.detector.continue_training(dummy_data, None)
        result_dict = json.loads(result)

        assert result_dict['status'] == 'success'
        assert result_dict['message'] == 'Continuous training simulation completed'  # Adjusted message

        # Check if new checkpoints were created in the temporary directory
        checkpoints = [f for f in os.listdir(self.temp_dir) if f.startswith('checkpoint_') and f.endswith('.pth')]
        assert len(checkpoints) > 1  # Should have more than the initial checkpoint

        # Verify that the model state has changed
        latest_checkpoint = self.detector.get_latest_checkpoint()
        self.detector.load_checkpoint(latest_checkpoint)
        final_state_dict = self.detector.model.state_dict()

        # Check if at least one parameter has changed
        params_changed = any(not torch.equal(initial_state_dict[key], final_state_dict[key]) 
                            for key in initial_state_dict.keys())
        assert params_changed, "Model parameters should have changed after training"

    def teardown_method(self, method):
        # Clean up temporary files and directories
        for root, dirs, files in os.walk(self.temp_dir, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(self.temp_dir)
