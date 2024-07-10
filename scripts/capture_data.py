import logging
import json
import hashlib
from scapy.all import IP, TCP, ICMP, Ether, sniff
from typing import List
import logging

from datetime import datetime

def ip_to_hash(ip: str) -> int:
    return int(hashlib.sha256(ip.encode()).hexdigest()[:8], 16)

def flags_to_int(flags: str) -> int:
    return sum((0x01 << i) for i, f in enumerate('FSRPAUEC') if f in flags)

def protocol_to_int(proto: int) -> int:
    # Simple mapping for common protocol numbers to a smaller range
    protocol_map = {1: 1,  # ICMP
                    6: 2,  # TCP
                    17: 3,  # UDP
                    2: 4}  # IGMP
    return protocol_map.get(proto, 0)  # Default to 0 if unknown

def translate_features(packet) -> List[int]:
    if Ether in packet:
        packet = packet[IP]

    ip_layer = packet[IP] if IP in packet else None
    tcp_layer = packet[TCP] if TCP in packet else None
    icmp_layer = packet[ICMP] if ICMP in packet else None

    timestamp = datetime.now().timestamp()  # Add timestamp as the first feature
    features = [
        timestamp,  # Unscaled timestamp
        ip_to_hash(ip_layer.src) if ip_layer else ip_to_hash("0.0.0.0"),
        ip_to_hash(ip_layer.dst) if ip_layer else ip_to_hash("0.0.0.0"),
        ip_layer.ttl if ip_layer else 0,
        protocol_to_int(ip_layer.proto) if ip_layer else 0,
        tcp_layer.sport if tcp_layer else 0,
        tcp_layer.dport if tcp_layer else 0,
        flags_to_int(tcp_layer.flags) if tcp_layer and hasattr(tcp_layer, 'flags') else 0,
        len(packet),  # Using packet length as a feature
    ]

    if icmp_layer:
        features.extend([icmp_layer.type, icmp_layer.code])
    else:
        features.extend([0, 0])  # Default to 0 if not ICMP

    return features

class NetworkTrafficCollector:
    def __init__(self, collection_point: str, dataset = None, file_path=None):
        self.collection_point = collection_point
        self.file_path = file_path
        logging.basicConfig(level=logging.DEBUG)

    def start_capture(self, timeout: int = None):
        try:
            logging.info(f"Starting traffic capture on {self.collection_point}...")
            if self.file_path:
                with open(self.file_path, 'w', encoding='utf-8') as file_writer:
                    sniff(iface=self.collection_point, prn=lambda pkt: self.packet_handler(pkt, file_writer), store=False, timeout=timeout)
            else:
                sniff(iface=self.collection_point, prn=self.packet_handler, store=False, timeout=timeout)
        except Exception as e:
            logging.error(f"Failed to start capture: {e}")
            raise RuntimeError("Failed to start packet capture") from e

    def packet_handler(self, packet, file_writer=None):
        try:
            logging.debug("Packet captured.")
            if self.packet_callback:
                self.packet_callback(packet)
            features = translate_features(packet)
            logging.debug(f"Packet features: {features}")
            
            # Add packet to live dataset
            if(self.dataset):
                self.dataset.add_packet((packet.time, features))
            
            if file_writer:
                json_line = json.dumps(features)
                file_writer.write(json_line + '\n')
        except Exception as e:
            logging.warning(f"Error processing packet: {e}")