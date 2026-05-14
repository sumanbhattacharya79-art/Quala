# API container: FastAPI on Cloud Run. PROJECT_ROOT = /app (matches app/backend/main.py parents).
FROM python:3.11-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/app

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-docker.txt .
RUN pip install --upgrade pip && pip install -r requirements-docker.txt

COPY app ./app
COPY backtesting ./backtesting
COPY data_input ./data_input
COPY data_output ./data_output
COPY migrations ./migrations

EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
