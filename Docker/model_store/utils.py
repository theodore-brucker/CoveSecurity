import numpy as np
from collections import deque

from torch.utils.data import Dataset
import torch

class PacketBuffer:
    def __init__(self, max_size, feature_dim):
        self.buffer = deque(maxlen=max_size)
        self.feature_dim = feature_dim
        self.np_buffer = None

    def add_packet(self, packet):
        if len(packet) != self.feature_dim:
            raise ValueError(f"Packet must have {self.feature_dim} features")
        self.buffer.append(packet)
        self.np_buffer = None  # Invalidate numpy buffer

    def get_sequence(self, sequence_length):
        if len(self.buffer) < sequence_length:
            return None
        if self.np_buffer is None or len(self.np_buffer) != len(self.buffer):
            self.np_buffer = np.array(self.buffer)
        return self.np_buffer[-sequence_length:]

    def clear(self):
        self.buffer.clear()
        self.np_buffer = None

    def get_latest_packet(self):
        return self.buffer[-1] if self.buffer else None

    def __len__(self):
        return len(self.buffer)

class PacketSequenceDataset(Dataset):
    def __init__(self, packet_buffer, sequence_length, feature_dim):
        self.packet_buffer = packet_buffer
        self.sequence_length = sequence_length
        self.feature_dim = feature_dim

    def __len__(self):
        return max(0, len(self.packet_buffer) - self.sequence_length + 1)

    def __getitem__(self, idx):
        sequence = self.packet_buffer.get_sequence(self.sequence_length)
        if sequence is None:
            raise IndexError("Not enough packets in buffer")
        return torch.tensor(sequence[idx:idx + self.sequence_length], dtype=torch.float32)

