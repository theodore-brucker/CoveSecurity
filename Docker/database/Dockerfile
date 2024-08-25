FROM mongo:latest

# Copy custom configuration file
COPY mongod.conf /etc/mongod.conf

# Copy initialization script
COPY init-mongo.js /docker-entrypoint-initdb.d/

# Set permissions for the init script
RUN chmod 755 /docker-entrypoint-initdb.d/init-mongo.js

# Expose the default MongoDB port
EXPOSE 27017

# Start MongoDB with the custom configuration
CMD ["mongod","--config", "/etc/mongod.conf"]
