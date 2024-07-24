import torch
from torch import nn
import math
from torch.nn.parameter import Parameter
from torch.nn import functional as F
import logging

class MemoryUnit(nn.Module):
    def __init__(self, mem_dim, fea_dim, shrink_thres=0.0025):
        super(MemoryUnit, self).__init__()
        self.mem_dim = mem_dim
        self.fea_dim = fea_dim
        logging.info(f"{self.mem_dim} x {self.fea_dim}")
        self.weight = Parameter(torch.Tensor(self.mem_dim, self.fea_dim))  # M x C
        self.bias = None
        self.shrink_thres = shrink_thres
        self.reset_parameters()

    def reset_parameters(self):
        stdv = 1. / math.sqrt(self.weight.size(1))
        self.weight.data.uniform_(-stdv, stdv)
        if self.bias is not None:
            self.bias.data.uniform_(-stdv, stdv)

    def forward(self, input):
        # Ensure input is the expected shape
        if input.dim() == 2 and input.size(1) == self.fea_dim:
            att_weight = F.linear(input, self.weight)  # Fea x Mem^T, (TxC) x (CxM) = TxM
            att_weight = F.softmax(att_weight, dim=1)  # TxM
            # ReLU based shrinkage, hard shrinkage for positive value
            if self.shrink_thres > 0:
                att_weight = hard_shrink_relu(att_weight, lambd=self.shrink_thres)
                att_weight = F.normalize(att_weight, p=1, dim=1)
            mem_trans = self.weight.permute(1, 0)  # Mem^T, MxC
            output = F.linear(att_weight, mem_trans)  # AttWeight x Mem^T^T = AW x Mem, (TxM) x (MxC) = TxC
            return output, att_weight
        else:
            raise ValueError("Input dimension mismatch. Expected input shape: [*, {}]".format(self.fea_dim))

    def extra_repr(self):
        return 'mem_dim={}, fea_dim={}'.format(self.mem_dim, self.fea_dim)

class MemModule(nn.Module):
    def __init__(self, mem_dim, fea_dim, shrink_thres=0.0025, device='cuda'):
        super(MemModule, self).__init__()
        self.mem_dim = mem_dim  # 120
        self.fea_dim = fea_dim  # 3
        self.shrink_thres = shrink_thres
        self.memory = MemoryUnit(self.mem_dim, self.fea_dim, self.shrink_thres)

    def forward(self, input):
        x = input.contiguous()  # [64, 3]
        y, att = self.memory(x)
        return y, att  # Return the output tensor and attention tensor directly
    
# relu based hard shrinkage function, only works for positive values
def hard_shrink_relu(input, lambd=0, epsilon=1e-12):
    output = (F.relu(input-lambd) * input) / (torch.abs(input - lambd) + epsilon)
    return output