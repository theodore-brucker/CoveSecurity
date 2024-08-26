db = db.getSiblingDB('network_sequences');

db.createCollection('sequences', {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      properties: {
        timestamp: {
          bsonType: "date",
          description: "Timestamp of the sequence"
        },
        sequence: {
          bsonType: ["array", "null"],
          description: "Array of feature sequences"
        },
        is_training: {
          bsonType: ["bool", "null"],
          description: "Whether the sequence was used for training"
        },
        human_readable: {
          bsonType: ["array", "null"],
          description: "Human readable representation of the sequence"
        },
        is_anomaly: {
          bsonType: ["bool", "null"],
          description: "Whether the sequence was detected as an anomaly"
        },
        reconstruction_error: {
          bsonType: ["double", "null"],
          description: "Reconstruction error from the model"
        },
        is_false_positive: {
          bsonType: ["bool", "null"],
          description: "Whether the sequence was manually marked as a false positive"
        }
      }
    }
  },
  validationLevel: "moderate"
});

db.sequences.createIndex({ timestamp: 1 });