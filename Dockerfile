FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY packages /app/packages
COPY scripts /app/scripts
COPY docs /app/docs
COPY config.example.json /app/config.example.json

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -e ".[rag-pgvector]"

EXPOSE 8787

CMD ["echoweave", "webhook", "--config", "/app/config.local.json"]
