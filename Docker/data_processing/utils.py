import json
import logging
import numpy as np
from decimal import Decimal
from scapy.fields import EDecimal
from datetime import datetime
import uuid

class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        try:
            if obj is None:
                return None
            elif isinstance(obj, (Decimal, EDecimal)):
                return str(obj)
            elif isinstance(obj, bytes):
                return obj.decode('utf-8', errors='replace')
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            elif isinstance(obj, (np.integer, np.floating)):
                return obj.item()
            elif isinstance(obj, datetime):
                return obj.isoformat()
            elif isinstance(obj, complex):
                return [obj.real, obj.imag]
            elif isinstance(obj, set):
                return list(obj)
            elif isinstance(obj, uuid.UUID):
                return str(obj)
            elif hasattr(obj, '__str__'):
                return str(obj)
            return super(CustomEncoder, self).default(obj)
        except Exception as e:
            logging.error(f"Error in CustomEncoder: {e} for object type {type(obj)}", exc_info=True)
            return str(obj)  # Fall back to string representation