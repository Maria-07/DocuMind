# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY requirements.txt constraints.txt ./
RUN pip install -r requirements.txt -c constraints.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

# /data/storage: uploaded files. /data/models: embedding/reranker/tokenizer downloads (a volume
# shared by API and worker, so models are fetched once, not on every container start).
RUN mkdir -p /data/storage /data/models && chown -R app:app /data /app
USER app

ENV STORAGE_LOCAL_PATH=/data/storage \
    MODEL_CACHE_DIR=/data/models \
    HF_HOME=/data/models/hf
EXPOSE 8000

# API by default; the worker service overrides the command (see infrastructure/docker-compose.yml).
# Proxy headers are honoured only from FORWARDED_ALLOW_IPS (uvicorn default 127.0.0.1); compose sets
# it because the backend is reachable from the frontend container alone.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--timeout-graceful-shutdown", "20"]
