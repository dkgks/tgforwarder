FROM python:3.11-slim

LABEL org.opencontainers.image.description="tgforwarder - Telegram Message Forwarder with AI Spam/Abuse Filter"

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Volume for persistent data (state, keywords, config, logs)
VOLUME ["/app/data"]

ENV CONFIG_PATH=/app/data/config.json

ENTRYPOINT ["tini", "--"]
CMD ["python3", "forwarder.py"]
