FROM python:3.9-slim

# Set up logging directory
RUN mkdir -p /var/log/traffic_generator
RUN touch /var/log/traffic_generator/startup.log

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY traffic_generator.py .

# Add startup logging
RUN echo "Installing additional utilities for debugging" >> /var/log/traffic_generator/startup.log
RUN apt-get update && \
    apt-get install -y iputils-ping net-tools && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    echo "Utilities installed successfully" >> /var/log/traffic_generator/startup.log

# Log Python and pip versions
RUN echo "Python version:" >> /var/log/traffic_generator/startup.log && \
    python --version >> /var/log/traffic_generator/startup.log && \
    echo "Pip version:" >> /var/log/traffic_generator/startup.log && \
    pip --version >> /var/log/traffic_generator/startup.log

# Log installed packages
RUN echo "Installed Python packages:" >> /var/log/traffic_generator/startup.log && \
    pip list >> /var/log/traffic_generator/startup.log

# Use ENTRYPOINT to ensure logs are captured
ENTRYPOINT ["/bin/sh", "-c", "echo 'Starting traffic generator' >> /var/log/traffic_generator/startup.log && python traffic_generator.py"]