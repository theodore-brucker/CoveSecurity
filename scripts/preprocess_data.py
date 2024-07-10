import re
import pandas as pd
from sklearn.preprocessing import MinMaxScaler
import os
import logging
import h5py

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def process_packets(input_path, output_path):
    """
    Process packet data from input_path, scale features, and save to output_path.

    Parameters:
    input_path (str): Path to the input raw data file.
    output_path (str): Path to the output processed data file.
    """
    try:
        # Read the raw data from the input file
        logging.info(f"Reading data from {input_path}")
        with open(input_path, 'r') as file:
            data = file.read()

        # Parse packet lines from the data
        packet_lines = data.strip().split('\n')

        # Function to parse a packet line
        def parse_packet(packet):
            match = re.match(r'Ether / IP / (TCP|UDP) ([\d.]+):(\w+) > ([\d.]+):(\w+) (\w+)', packet)
            if match:
                protocol, src_ip, src_port, dst_ip, dst_port, flags = match.groups()
                return src_ip, src_port, dst_ip, dst_port, protocol, flags
            return None

        # Parse all packet lines
        packets = [parse_packet(line) for line in packet_lines if parse_packet(line)]
        logging.info(f"Parsed {len(packets)} packets")

        # Create a DataFrame
        df = pd.DataFrame(packets, columns=['src_ip', 'src_port', 'dst_ip', 'dst_port', 'protocol', 'flags'])

        # Function to convert IP to numeric
        def ip_to_numeric(ip):
            return int(ip.replace('.', ''))

        # Function to convert port to numeric
        def port_to_numeric(port):
            try:
                if port.lower() == 'https':
                    return 443
                return int(port, 16) if '0x' in port else int(port)
            except:
                return 1

        # Convert IP addresses to numeric
        df['src_ip'] = df['src_ip'].apply(ip_to_numeric)
        df['dst_ip'] = df['dst_ip'].apply(ip_to_numeric)

        # Encode protocol and flags as numeric categories
        df['protocol'] = df['protocol'].astype('category').cat.codes
        df['flags'] = df['flags'].astype('category').cat.codes

        # Convert ports to integers
        df['src_port'] = df['src_port'].apply(port_to_numeric)
        df['dst_port'] = df['dst_port'].apply(port_to_numeric)

        # Scale features to [0, 1]
        scaler = MinMaxScaler()
        scaled_df = pd.DataFrame(scaler.fit_transform(df), columns=df.columns)

        # Ensure the directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Save the scaled DataFrame to an HDF5 file
        with h5py.File(output_path, 'w') as hf:
            hf.create_dataset('packets', data=scaled_df.values)
        logging.info(f"Scaled data written to {output_path}")

    except FileNotFoundError as e:
        logging.error(f"File not found: {e}")
    except Exception as e:
        logging.error(f"An error occurred: {e}")


