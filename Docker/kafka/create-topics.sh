#!/bin/bash

# Wait for Kafka to be ready
echo "Waiting for Kafka to be ready..."
sleep 10

# Create topics with correct configurations
echo "Creating Kafka Connect topics..."
kafka-topics --create --if-not-exists --bootstrap-server kafka:9092 --topic docker-connect-configs --replication-factor 1 --partitions 1 --config cleanup.policy=compact
kafka-topics --create --if-not-exists --bootstrap-server kafka:9092 --topic docker-connect-offsets --replication-factor 1 --partitions 1 --config cleanup.policy=compact
kafka-topics --create --if-not-exists --bootstrap-server kafka:9092 --topic docker-connect-status --replication-factor 1 --partitions 1 --config cleanup.policy=compact

echo "Kafka Connect topics created."