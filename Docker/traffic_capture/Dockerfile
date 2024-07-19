# Use an official Python runtime as a parent image
FROM python:3.8-slim

# Set the working directory
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . /app

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Ensure the model directory exists
RUN mkdir -p /app/model

# Copy the minmax scaler file
COPY model/minmaxscaler.pkl /app/model/minmaxscaler.pkl

# Install curl
RUN apt-get update && apt-get install -y curl

# List the contents of the entire /app directory for debugging
RUN ls -R /app

# Make port 5000 available to the world outside this container
EXPOSE 5000

# Run app.py when the container launches
CMD ["python", "app.py"]

