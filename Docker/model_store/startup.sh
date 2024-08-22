#!/bin/bash

# Your custom startup commands
sleep 5

# Example of deregistering the existing model if it exists
MODEL_NAME="transformer_autoencoder"
MODEL_VERSION="1.0"

# Check if the model version is already registered
EXISTING_MODEL=$(curl -s -X GET "http://localhost:8081/models/${MODEL_NAME}/${MODEL_VERSION}")
if [[ $EXISTING_MODEL == *"${MODEL_NAME}"* ]]; then
  echo "Deregistering existing model version ${MODEL_VERSION} for model ${MODEL_NAME}"
  curl -X DELETE "http://localhost:8081/models/${MODEL_NAME}/${MODEL_VERSION}"
fi

# Register the new model
curl -X POST "http://localhost:8081/models?url=${MODEL_NAME}.mar&initial_workers=1&synchronous=true"

REGISTRY_PATH="/home/model-server/model-registry/model_registry.json"
CHECKPOINT_PATH="/home/model-server/checkpoints/transformer_autoencoder_1724018419_49.pth"

if [ ! -s "$REGISTRY_PATH" ]; then
    echo "Adding dummy entry to model_registry.json"
    cat <<EOF > "$REGISTRY_PATH"
{
    "v1": {
        "checkpoint_path": "$CHECKPOINT_PATH",
        "performance_metrics": {
            "validation_loss": 0.9999
        },
        "creation_date": "$(date +"%Y-%m-%d %H:%M:%S")"
    },
    "latest": "v1"
}
EOF
fi

# Starting TorchServe
torchserve --start --model-store /home/model-server/model-store --ts-config /home/model-server/config.properties