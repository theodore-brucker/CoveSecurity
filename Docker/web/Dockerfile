FROM python:3.8-slim

WORKDIR /app

COPY requirements.txt requirements.txt
COPY app.py app.py
COPY templates /app/templates

RUN apt-get update && \
    apt-get install -y tshark && \
    pip install --no-cache-dir -r requirements.txt

EXPOSE 5000 8000

CMD ["python", "app.py"]


