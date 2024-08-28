# Use the official TorchServe base image
FROM pytorch/torchserve:latest

# Set the working directory in the container
WORKDIR /home/model-server

USER root

# Install curl
RUN apt-get update

# Copy the custom handler and any other necessary files
COPY custom_handler.py /home/model-server/
COPY transformer_autoencoder.py /home/model-server/
COPY transformer_autoencoder.mar /home/model-server/model-store/
COPY config.properties /home/model-server/config.properties
COPY model_registry.json /home/model-server/model-registry/
COPY /config/ /home/model-server/

RUN mkdir -p /home/model-server/checkpoints/

# Install any needed packages specified in requirements.txt
COPY requirements.txt /home/model-server/
COPY startup.sh /home/model-server/startup.sh

RUN sed -i 's/\r$//' /home/model-server/startup.sh
RUN chmod +x /home/model-server/startup.sh
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install torch-model-archiver


# Expose the ports TorchServe listens on
EXPOSE 8080 8081 8082

CMD ["/home/model-server/startup.sh"]