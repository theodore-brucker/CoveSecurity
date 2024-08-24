import json
import numpy as np
from decimal import Decimal
from scapy.fields import EDecimal

class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (Decimal, EDecimal)):
            return float(obj)
        elif isinstance(obj, bytes):
            return obj.decode('utf-8', errors='replace')
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, (np.int_, np.intc, np.intp, np.int8, np.int16, np.int32, np.int64, np.uint8, np.uint16, np.uint32, np.uint64)):
            return int(obj)
        elif isinstance(obj, (np.float_, np.float16, np.float32, np.float64)):
            return float(obj)
        elif hasattr(obj, '__str__'):
            return str(obj)
        return super(CustomEncoder, self).default(obj)
