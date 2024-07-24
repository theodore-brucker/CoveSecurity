import logging
import torch
import torch.nn as nn
import torch.nn.functional as F
from memory_module import MemModule

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MEMAE(nn.Module):
    def __init__(self, input_size=6, hidden_size=60, num_layers=2, sequence_length=32):
        super(MEMAE, self).__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.sequence_length = sequence_length

        self.encoder = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        self.encoder_fc = nn.Linear(hidden_size, 3)

        mem_dim = 3
        self.mem_rep = MemModule(mem_dim=mem_dim, fea_dim=3)

        self.decoder_fc = nn.Linear(3, hidden_size)
        self.decoder = nn.LSTM(hidden_size, hidden_size, num_layers, batch_first=True)
        self.output_layer = nn.Linear(hidden_size, input_size)

        logger.info("MEMAE model initialized with LSTM layers")

    def forward(self, x):
        logger.debug(f"Input shape: {x.shape}")

        # Encoder
        encoder_output, (hidden, cell) = self.encoder(x)
        logger.debug(f"Encoder output shape: {encoder_output.shape}")

        # Use the last hidden state
        f = self.encoder_fc(encoder_output[:, -1, :])
        logger.debug(f"Encoded shape: {f.shape}")

        # Memory module
        f, att = self.mem_rep(f)
        logger.debug(f"Memory module output shape: {f.shape}")
        logger.debug(f"Attention weights shape: {att.shape}")

        # Decoder
        f = self.decoder_fc(f)
        f = f.unsqueeze(1).repeat(1, x.size(1), 1)
        decoder_output, _ = self.decoder(f)
        logger.debug(f"Decoder output shape: {decoder_output.shape}")

        output = self.output_layer(decoder_output)
        logger.debug(f"Output shape: {output.shape}")

        return output, att

    def compute_loss(self, outputs, target):
        output, att = outputs
        recon_loss = F.mse_loss(output, target)
        entropy_loss = torch.mean((-att) * torch.log(att + 1e-12))
        loss = recon_loss + 0.0002 * entropy_loss
        logger.debug(f"Reconstruction loss: {recon_loss.item()}, Entropy loss: {entropy_loss.item()}")
        return loss

    def compute_batch_error(self, outputs, target):
        output, _ = outputs
        loss = F.mse_loss(output, target, reduction='none').mean(dim=(1, 2))
        logger.debug(f"Batch error shape: {loss.shape}")
        return loss

    def compute_anomaly_score(self, x):
        outputs = self.forward(x)
        output, att = outputs
        reconstruction_error = F.mse_loss(output, x, reduction='none').mean(dim=(1, 2))
        logger.debug(f"Anomaly score shape: {reconstruction_error.shape}")
        return reconstruction_error, att