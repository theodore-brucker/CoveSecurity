import json
import logging
import numpy as np
from decimal import Decimal
from scapy.fields import EDecimal

class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        try:
            if isinstance(obj, (Decimal, EDecimal)):
                return float(obj)
            elif isinstance(obj, bytes):
                return obj.decode('utf-8', errors='replace')
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            elif isinstance(obj, np.integer):
                return int(obj)
            elif isinstance(obj, np.floating):
                return float(obj)
            elif hasattr(obj, '__str__'):
                return str(obj)
            return super(CustomEncoder, self).default(obj)
        except Exception as e:
            logging.error(f"Error in CustomEncoder: {e} for object type {type(obj)}", exc_info=True)
            raise