import tensorflow as tf

# Define the Autoencoder model
def create_autoencoder(input_dim):
    input_layer = tf.keras.layers.Input(shape=(input_dim,))
    encoded = tf.keras.layers.Dense(128, activation='relu')(input_layer)
    decoded = tf.keras.layers.Dense(input_dim, activation='sigmoid')(encoded)
    autoencoder = tf.keras.models.Model(inputs=input_layer, outputs=decoded)
    return autoencoder