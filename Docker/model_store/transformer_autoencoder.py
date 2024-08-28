import logging
import math
import torch
import torch.nn as nn
import torch.nn.functional as F

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TransformerAutoencoder(nn.Module):
    def __init__(self, input_size=12, sequence_length=16, nhead=2, num_encoder_layers=3, num_decoder_layers=3, dim_feedforward=256, dropout=0.1):
        super(TransformerAutoencoder, self).__init__()
        self.input_size = input_size
        self.sequence_length = sequence_length

        self.pos_encoder = PositionalEncoding(input_size, dropout, max_len=sequence_length)
        self.encoder_layer = nn.TransformerEncoderLayer(d_model=input_size, nhead=nhead, dim_feedforward=dim_feedforward, dropout=dropout)
        self.transformer_encoder = nn.TransformerEncoder(self.encoder_layer, num_layers=num_encoder_layers)
        
        self.decoder_layer = nn.TransformerDecoderLayer(d_model=input_size, nhead=nhead, dim_feedforward=dim_feedforward, dropout=dropout)
        self.transformer_decoder = nn.TransformerDecoder(self.decoder_layer, num_layers=num_decoder_layers)
        
        self.fc = nn.Linear(input_size, input_size)

    def forward(self, src):
        # src shape: [batch_size, sequence_length, input_size]
        logger.debug(f"Input shape in forward: {src.shape}")
        src = src.transpose(0, 1)  # [sequence_length, batch_size, input_size]
        logger.debug(f"Transposed input shape: {src.shape}")
        
        src = self.pos_encoder(src)
        memory = self.transformer_encoder(src)
        output = self.transformer_decoder(memory, memory)
        
        output = self.fc(output)
        return output.transpose(0, 1), None  # [batch_size, sequence_length, input_size], None for compatibility

    def compute_loss(self, outputs, inputs):
        output, _ = outputs
        loss = F.smooth_l1_loss(output, inputs)
        logger.debug(f"Reconstruction loss: {loss.item()}")
        return loss

    def compute_batch_error(self, outputs, target):
        output, _ = outputs
        loss = F.smooth_l1_loss(output, target, reduction='none').mean(dim=(1, 2))
        logger.debug(f"Batch error shape: {loss.shape}")
        return loss

    def compute_anomaly_score(self, x):
        outputs, _ = self.forward(x)
        reconstruction_error = F.smooth_l1_loss(outputs, x, reduction='none').mean(dim=(1, 2))
        logger.debug(f"Anomaly score shape: {reconstruction_error.shape}")
        return reconstruction_error, None

    def compute_classification_metrics(self, output, inputs, targets):
        recon_errors = F.smooth_l1_loss(output, inputs, reduction='none').mean(dim=(1, 2))
        normal_loss = recon_errors[targets == 0].mean().item() if torch.sum(targets == 0) > 0 else 0.0
        malicious_loss = recon_errors[targets == 1].mean().item() if torch.sum(targets == 1) > 0 else 0.0
        return normal_loss, malicious_loss

class PositionalEncoding(nn.Module):
    def __init__(self, d_model, dropout=0.1, max_len=5000):
        super(PositionalEncoding, self).__init__()
        self.dropout = nn.Dropout(p=dropout)
        self.d_model = d_model

        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(0).transpose(0, 1)
        self.register_buffer('pe', pe)

    def forward(self, x):
        logger.debug(f"Input shape: {x.shape}")
        logger.debug(f"PE shape: {self.pe.shape}")
        logger.debug(f"d_model: {self.d_model}")
        
        if x.size(-1) != self.d_model:
            raise ValueError(f"Expected input to have {self.d_model} features, but got {x.size(-1)}")
        
        x = x + self.pe[:x.size(0), :]
        return self.dropout(x)