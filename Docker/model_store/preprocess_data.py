import re
import pandas as pd
from sklearn.preprocessing import MinMaxScaler
import os
import logging
import h5py
import pickle
import torch
from torch.utils.data import Dataset, DataLoader

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def process_packets(input_path, output_path, scaler_path):
    """
    Process packet data from input_path, scale features, and save to output_path.

    Parameters:
    input_path (str): Path to the input raw data file.
    output_path (str): Path to the output processed data file.
    scaler_path (str): Path to save the fitted scaler.
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
            match = re.match(r'Ether / IP / (TCP|UDP|ICMP) ([\d.]+):(\w+) > ([\d.]+):(\w+) (\w+)', packet)
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

        ordered_columns = ['src_ip', 'src_port', 'dst_ip', 'dst_port', 'protocol', 'flags']
        df = df[ordered_columns]

        # Scale features to [0, 1]
        scaler = MinMaxScaler()
        scaled_df = pd.DataFrame(scaler.fit_transform(df), columns=df.columns)

        # Ensure the directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Save the scaled DataFrame to an HDF5 file
        with h5py.File(output_path, 'w') as hf:
            hf.create_dataset('packets', data=scaled_df.values)
        logging.info(f"Scaled data written to {output_path}")

        # Save the scaler
        with open(scaler_path, 'wb') as f:
            pickle.dump(scaler, f)
        logging.info(f"Scaler saved to {scaler_path}")

    except FileNotFoundError as e:
        logging.error(f"File not found: {e}")
    except Exception as e:
        logging.error(f"An error occurred: {e}")

def create_dataloader(hdf5_file, batch_size=32, shuffle=True):
    dataset = PacketDataset(hdf5_file)
    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=shuffle)
    return dataloader

class PacketDataset(Dataset):
    def __init__(self, hdf5_file):
        self.hdf5_file = hdf5_file
        with h5py.File(hdf5_file, 'r') as f:
            self.data = f['packets'][:]
        self.data = torch.tensor(self.data, dtype=torch.float32)

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        return self.data[idx]

if __name__ == "__main__":
    input_path = 'data/raw/your_raw_data_file.txt'  # Update with your actual raw data file path
    output_path = 'data/processed/processed_data.h5'  # Update with your actual output file path
    scaler_path = 'model/minmaxscaler.pkl'  # Path to save the scaler
    process_packets(input_path, output_path, scaler_path)
