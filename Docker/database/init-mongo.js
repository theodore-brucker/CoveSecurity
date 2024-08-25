db = db.getSiblingDB('network_sequences');

db.createCollection('sequences', {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["sequence_id", "timestamp", "packets", "is_anomaly", "is_training_data", "manual_classification"],
      properties: {
        sequence_id: {
          bsonType: "string",
          description: "Unique identifier for the sequence"
        },
        timestamp: {
          bsonType: "date",
          description: "Timestamp of the sequence"
        },
        packets: {
          bsonType: "array",
          description: "Array of packets in the sequence"
        },
        is_anomaly: {
          bsonType: "bool",
          description: "Whether the sequence was detected as an anomaly"
        },
        is_training_data: {
          bsonType: "bool",
          description: "Whether the sequence was used for training"
        },
        manual_classification: {
          bsonType: ["bool", "null"],
          description: "Manual classification of false positive (null if not classified)"
        }
      }
    }
  }
});

db.sequences.createIndex({ sequence_id: 1 }, { unique: true });
db.sequences.createIndex({ timestamp: 1 });
db.sequences.createIndex({ is_anomaly: 1 });
db.sequences.createIndex({ is_training_data: 1 });