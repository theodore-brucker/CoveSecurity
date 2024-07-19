import json
import torch
from torch import nn, optim
from preprocess_data import create_dataloader

class Autoencoder(nn.Module):
    def __init__(self, input_dim):
        super(Autoencoder, self).__init__()
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, 64),
            nn.ReLU(),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, 16)
        )
        self.decoder = nn.Sequential(
            nn.Linear(16, 32),
            nn.ReLU(),
            nn.Linear(32, 64),
            nn.ReLU(),
            nn.Linear(64, input_dim)
        )
        
    def forward(self, x):
        encoded = self.encoder(x)
        decoded = self.decoder(encoded)
        return decoded

def train_autoencoder(hdf5_file, batch_size=32, num_epochs=10, learning_rate=0.001):
    # Create DataLoader
    dataloader = create_dataloader(hdf5_file, batch_size)

    # Initialize the autoencoder
    input_dim = dataloader.dataset[0].shape[0]
    model = Autoencoder(input_dim)

    # Define the loss function and the optimizer
    criterion = nn.MSELoss()
    optimizer = optim.Adam(model.parameters(), lr=learning_rate)

    # Training loop
    for epoch in range(num_epochs):
        for data in dataloader:
            # Zero the gradients
            optimizer.zero_grad()
            # Forward pass
            outputs = model(data)
            # Calculate the loss
            loss = criterion(outputs, data)
            # Backward pass and optimize
            loss.backward()
            optimizer.step()
        
        print(f'Epoch [{epoch+1}/{num_epochs}], Loss: {loss.item():.4f}')

    # Return the trained model
    return model

def evaluate_autoencoder(model, hdf5_file, batch_size=32):
    # Create DataLoader
    dataloader = create_dataloader(hdf5_file, batch_size, shuffle=False)

    # Set the model to evaluation mode
    model.eval()

    # Evaluation loop
    total_loss = 0
    criterion = nn.MSELoss()
    with torch.no_grad():
        for data in dataloader:
            outputs = model(data)
            loss = criterion(outputs, data)
            total_loss += loss.item()
    
    avg_loss = total_loss / len(dataloader)
    print(f'Average Loss: {avg_loss:.4f}')
    return avg_loss

def store_autoencoder(model, path):
    torch.save(model.state_dict(), path)
    print(f'Model saved to {path}')
