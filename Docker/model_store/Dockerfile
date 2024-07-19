# Use the official TorchServe base image
FROM pytorch/torchserve:latest

# Copy the requirements file into the container
COPY requirements.txt /home/model-server/requirements.txt

# Install the dependencies
RUN pip install -r /home/model-server/requirements.txt

# Copy the model archive and configuration file into the container
COPY autoencoder.mar /home/model-server/model-store/autoencoder.mar
COPY config.properties /home/model-server/config.properties

# Set the entrypoint to start TorchServe with the model and configuration file
CMD ["torchserve", "--start", "--model-store", "/home/model-server/model-store", "--models", "autoencoder=autoencoder.mar", "--ts-config", "/home/model-server/config.properties"]

