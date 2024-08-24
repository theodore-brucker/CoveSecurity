import socket
import random
import time

target_ip = "172.17.162.128"
ports = [80, 443]

def send_benign_traffic():
    benign_data = [
        "GET / HTTP/1.1\r\nHost: example.com\r\n\r\n",
        "GET /index.html HTTP/1.1\r\nHost: example.com\r\n\r\n",
        "GET /about HTTP/1.1\r\nHost: example.com\r\n\r\n"
    ]
    data = random.choice(benign_data)
    port = random.choice(ports)
    
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect((target_ip, port))
            s.sendall(data.encode())
        print(f"Sent benign traffic to port {port}")
    except Exception as e:
        print(f"Error sending benign traffic to port {port}: {e}")

def send_malicious_traffic():
    malicious_data = [
        "GET /../../../etc/passwd HTTP/1.1\r\nHost: evil.com\r\n\r\n",
        "POST /login HTTP/1.1\r\nHost: victim.com\r\nContent-Type: application/x-www-form-urlencoded\r\n\r\nusername=admin&password='; DROP TABLE users;--",
        "GET /?exec=rm+-rf+/ HTTP/1.1\r\nHost: target.com\r\n\r\n",
        "GET / HTTP/1.1\r\nHost: xss.com\r\nUser-Agent: <script>alert('XSS')</script>\r\n\r\n"
    ]
    data = random.choice(malicious_data)
    port = random.choice(ports)
    
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect((target_ip, port))
            s.sendall(data.encode())
        print(f"Sent malicious traffic to port {port}")
    except Exception as e:
        print(f"Error sending malicious traffic to port {port}: {e}")

def main():
    while True:
        if random.random() < 1:  # chance of benign traffic
            send_benign_traffic()
        else:
            send_malicious_traffic()
        time.sleep(.1)  # Wait between 1 and 2 seconds

if __name__ == "__main__":
    main()