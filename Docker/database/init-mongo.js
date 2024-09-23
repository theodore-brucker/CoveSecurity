// Switch to the network_sequences database
db = db.getSiblingDB('network_sequences');

// Drop the existing database if it exists
db.dropDatabase();

db.createCollection('sequences', {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["_id", "timestamp", "sequence", "human_readable", "is_anomaly", "is_training", "is_false_positive"],
      properties: {
        _id: {
          bsonType: "string",
          description: "Unique identifier for the sequence document"
        },
        timestamp: {
          bsonType: "string",
          description: "Timestamp of the sequence as ISO 8601 string"
        },
        sequence: {
          bsonType: "array",
          description: "Array of feature sequences"
        },
        human_readable: {
          bsonType: "array",
          description: "Human readable representation of the sequence"
        },
        is_anomaly: {
          bsonType: "bool",
          description: "Whether the sequence was detected as an anomaly"
        },
        is_training: {
          bsonType: "bool",
          description: "Whether the sequence was used for training"
        },
        is_false_positive: {
          bsonType: "bool",
          description: "Whether the sequence was manually marked as a false positive"
        },
        reconstruction_error: {
          bsonType: ["double", "null"],
          description: "Reconstruction error from the model"
        },
        familiarity: {
          bsonType: ["double", "null"],
          description: "Familiarity score of the sequence"
        }
      }
    }
  },
  validationLevel: "warn"
});

db.sequences.createIndex({ timestamp: 1 });