# Use Python 3.8 slim image as the base
FROM python:3.8-slim
# Set environment variable to avoid prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Set the working directory in the container
WORKDIR /app

# Update package lists and clean up to reduce image size
RUN apt-get update && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy all files from the current directory to /app in the container
COPY . /app

# Install Python dependencies from requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Log message (for debugging)
RUN echo "Exposing port 5001"
# Expose port 5001 for the application
EXPOSE 5001

# Set the command to run when the container starts
CMD ["python", "main.py"]

