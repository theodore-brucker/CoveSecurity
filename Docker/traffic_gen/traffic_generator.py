import time
import random
import requests
import scapy.all as scapy
import os
import logging
import socket
from datetime import datetime, timedelta

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("/var/log/traffic_generator/runtime.log"),
        logging.StreamHandler()
    ]
)

TARGET_IP = os.getenv('TARGET_IP', '172.17.0.1')
TARGET_PORT = int(os.getenv('TARGET_PORT', 5000))

def check_connectivity():
    logging.info(f"Checking connectivity to {TARGET_IP}:{TARGET_PORT}")
    
    # Check TCP connectivity
    try:
        sock = socket.create_connection((TARGET_IP, TARGET_PORT), timeout=5)
        sock.close()
        logging.info(f"Successfully connected to {TARGET_IP}:{TARGET_PORT}")
        return True
    except socket.error as e:
        logging.error(f"Failed to connect to {TARGET_IP}:{TARGET_PORT}: {e}")
    
    # If TCP fails, try ICMP
    try:
        result = scapy.sr1(scapy.IP(dst=TARGET_IP)/scapy.ICMP(), timeout=5, verbose=False)
        if result:
            logging.info(f"ICMP ping to {TARGET_IP} successful")
            return True
        else:
            logging.error(f"ICMP ping to {TARGET_IP} failed: No response")
    except Exception as e:
        logging.error(f"ICMP ping to {TARGET_IP} failed: {e}")
    
    # Troubleshooting suggestions
    logging.error("Connectivity check failed. Please check the following:")
    logging.error("1. Ensure the target IP and port are correct in the environment variables.")
    logging.error("2. Check if the target service is running and listening on the specified port.")
    logging.error("3. Verify network settings and firewall rules.")
    logging.error("4. If using Docker, ensure the network mode is correctly configured.")
    
    return False

def generate_normal_traffic():
    logging.info("Generating normal traffic")
    
    # HTTP GET request
    try:
        response = requests.get(f"http://{TARGET_IP}:{TARGET_PORT}/")
        logging.info(f"HTTP GET request sent. Status code: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send HTTP GET request: {e}")

    # ICMP ping
    try:
        result = scapy.sr1(scapy.IP(dst=TARGET_IP)/scapy.ICMP(), timeout=2, verbose=False)
        if result:
            logging.info(f"ICMP ping successful. Round trip time: {result.time*1000:.2f}ms")
        else:
            logging.warning("ICMP ping sent but no response received")
    except Exception as e:
        logging.error(f"Failed to send ICMP ping: {e}")

    # TCP SYN
    try:
        result = scapy.sr1(scapy.IP(dst=TARGET_IP)/scapy.TCP(dport=TARGET_PORT, flags="S"), timeout=2, verbose=False)
        if result:
            logging.info(f"TCP SYN sent. Response flags: {result.sprintf('%TCP.flags%')}")
        else:
            logging.warning("TCP SYN sent but no response received")
    except Exception as e:
        logging.error(f"Failed to send TCP SYN: {e}")

def generate_malicious_traffic():
    logging.info("Generating malicious traffic")
    
    # Port scan
    open_ports = []
    for port in random.sample(range(1, 1001), 10):
        try:
            result = scapy.sr1(scapy.IP(dst=TARGET_IP)/scapy.TCP(dport=port, flags="S"), timeout=1, verbose=False)
            if result and result.haslayer(scapy.TCP) and result.getlayer(scapy.TCP).flags == 0x12:
                open_ports.append(port)
            scapy.send(scapy.IP(dst=TARGET_IP)/scapy.TCP(dport=port, flags="R"), verbose=False)
        except Exception as e:
            logging.error(f"Failed to send port scan packet to port {port}: {e}")
    logging.info(f"Port scan completed. Open ports found: {open_ports}")

    # UDP flood
    sent_packets = 0
    for _ in range(100):
        try:
            scapy.send(scapy.IP(dst=TARGET_IP)/scapy.UDP(dport=random.randint(1, 65535))/("X"*1024), verbose=False)
            sent_packets += 1
        except Exception as e:
            logging.error(f"Failed to send UDP flood packet: {e}")
    logging.info(f"UDP flood completed. Sent {sent_packets} packets")

    # Malformed packets
    try:
        scapy.send(scapy.IP(dst=TARGET_IP, ihl=2)/scapy.TCP(), verbose=False)
        logging.info("Malformed packet sent")
    except Exception as e:
        logging.error(f"Failed to send malformed packet: {e}")

def main():
    logging.info("Traffic generator started")
    
    if not check_connectivity():
        logging.error("Exiting due to connectivity issues")
        return

    start_time = datetime.now()
    normal_traffic_end_time = start_time + timedelta(minutes=3)
    cycle_count = 0
    
    try:
        while True:
            current_time = datetime.now()
            cycle_count += 1
            
            if current_time < normal_traffic_end_time:
                generate_normal_traffic()
                logging.info(f"Normal traffic cycle {cycle_count} completed. Time remaining for normal traffic: {(normal_traffic_end_time - current_time).seconds} seconds")
            else:
                if random.random() < 0.7:  # 70% chance of normal traffic after the initial 3 minutes
                    generate_normal_traffic()
                else:
                    generate_malicious_traffic()
                logging.info(f"Mixed traffic cycle {cycle_count} completed")
            
            sleep_time = random.uniform(0.5, 2)
            time.sleep(sleep_time)
            
            # Log summary every 10 minutes
            if cycle_count % 300 == 0:  # Assuming average cycle time of 2 seconds, 300 cycles ≈ 10 minutes
                elapsed_time = (datetime.now() - start_time).total_seconds()
                logging.info(f"Summary: Completed {cycle_count} cycles in {elapsed_time:.2f} seconds. Average cycle time: {elapsed_time/cycle_count:.2f} seconds")
    
    except KeyboardInterrupt:
        logging.info("Traffic generator stopped by user")
    except Exception as e:
        logging.error(f"Unexpected error occurred: {e}")
    finally:
        logging.info("Traffic generator shutting down")

if __name__ == "__main__":
    main()