FROM python:3.8-slim

WORKDIR /app

COPY requirements.txt requirements.txt
COPY app.py app.py

RUN apt-get update && \
    apt-get install -y  && \
    pip install --upgrade kafka-python && \
    pip install --no-cache-dir -r requirements.txt

# Run app.py when the container launches
CMD ["python", "app.py"]

