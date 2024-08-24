import logging
import os
import numpy as np
import ipaddress
from scapy.all import IP, TCP, UDP


FEATURE_COUNT = int(os.getenv('FEATURE_COUNT', 12))
protocol_map = {1: "ICMP", 6: "TCP", 17: "UDP", 2: "IGMP"}

def inspect_packet(packet):
    """Log detailed information about a packet for debugging."""
    logging.debug(f"Packet summary: {packet.summary()}")
    if IP in packet:
        ip = packet[IP]
        logging.debug(f"IP fields: {ip.fields}")
        logging.debug(f"IP flags type: {type(ip.flags)}, value: {ip.flags}")
    if TCP in packet:
        tcp = packet[TCP]
        logging.debug(f"TCP fields: {tcp.fields}")
    elif UDP in packet:
        udp = packet[UDP]
        logging.debug(f"UDP fields: {udp.fields}")

def ip_to_normalized_feature(ip_str):
    ip_int = int(ipaddress.ip_address(ip_str))
    return (ip_int / (2**32 - 1)) * 2 - 1  # Map to [-1, 1] range

def process_packet(packet):
    features = np.zeros(FEATURE_COUNT, dtype=np.float32)
    human_readable = {}

    try:
        if IP in packet:
            ip = packet[IP]
            inspect_packet(packet)  # Log detailed packet info for debugging
            
            try:
                # Convert IP addresses to normalized features
                features[0] = ip_to_normalized_feature(ip.src)
                features[1] = ip_to_normalized_feature(ip.dst)
                features[2] = (np.log1p(float(ip.len)) / np.log1p(65535)) * 2 - 1  # Map to [-1, 1]
                features[3] = int(ip.flags) / 3.5 - 1  # Map to [-1, 1] assuming max flag value is 7
                features[4] = float(ip.ttl) / 127.5 - 1  # Map to [-1, 1]
                features[5] = float(ip.proto) / 127.5 - 1  # Map to [-1, 1]
                human_readable.update({
                    'src_ip': ip.src,
                    'dst_ip': ip.dst,
                    'length': int(ip.len),  # Convert to int
                    'flags': int(ip.flags),
                    'ttl': int(ip.ttl),  # Convert to int
                    'protocol': protocol_map.get(ip.proto, "Unknown")
                })
            except Exception as e:
                logging.error(f"Error processing IP packet fields: {e}")
                return None, None, None

            if TCP in packet:
                tcp = packet[TCP]
                try:
                    features[6] = (np.log1p(float(tcp.sport)) / np.log1p(65535)) * 2 - 1
                    features[7] = (np.log1p(float(tcp.dport)) / np.log1p(65535)) * 2 - 1
                    features[8] = int(tcp.flags) / 127.5 - 1  # Map to [-1, 1]
                    features[9] = (np.log1p(float(tcp.window)) / np.log1p(65535)) * 2 - 1
                    features[10] = -1  # Placeholder for consistency
                    human_readable.update({
                        'src_port': int(tcp.sport),  # Convert to int
                        'dst_port': int(tcp.dport),  # Convert to int
                        'tcp_flags': int(tcp.flags),
                        'window': int(tcp.window)  # Convert to int
                    })
                except Exception as e:
                    logging.error(f"Error processing TCP packet fields: {e}")
                    return None, None, None
            elif UDP in packet:
                udp = packet[UDP]
                try:
                    features[6] = (np.log1p(float(udp.sport)) / np.log1p(65535)) * 2 - 1
                    features[7] = (np.log1p(float(udp.dport)) / np.log1p(65535)) * 2 - 1
                    features[8] = (np.log1p(float(udp.len)) / np.log1p(65535)) * 2 - 1
                    features[9] = -1  # Placeholder for consistency
                    features[10] = -1  # Placeholder for consistency
                    human_readable.update({
                        'src_port': int(udp.sport),  # Convert to int
                        'dst_port': int(udp.dport),  # Convert to int
                        'udp_len': int(udp.len)  # Convert to int
                    })
                except Exception as e:
                    logging.error(f"Error processing UDP packet fields: {e}")
                    return None, None, None
            else:
                features[6:11] = -1  # Set to -1 if neither TCP nor UDP
                human_readable.update({
                    'src_port': 0,
                    'dst_port': 0,
                    'flags_or_len': 0,
                    'window_or_padding': 0
                })

            features[11] = (np.log1p(float(len(packet))) / np.log1p(65535)) * 2 - 1
            human_readable['packet_size'] = int(len(packet))  # Convert to int

            return features, human_readable, packet.time
        else:
            logging.warning("Packet does not contain IP layer")
            return None, None, None
    except Exception as e:
        logging.error(f"Unexpected error in process_packet: {e}", exc_info=True)
        return None, None, None