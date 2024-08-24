# Use an official Python runtime as a parent image
FROM python:3.8-slim

ENV DEBIAN_FRONTEND=noninteractive

# Set the working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy the current directory contents into the container at /app
COPY . /app

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install netifaces kafka-python

RUN echo "Exposing port 5001"
# Make port 5001 available to the world outside this container
EXPOSE 5001

RUN echo "Running main.py"

# Run app.py when the container launches
CMD ["python", "main.py"]

