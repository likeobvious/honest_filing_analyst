# ── PS5 Honest Filing Analyst — Multi-Stage Dockerfile ──────────────────────────
# Target: Azure Standard B2s (2 vCPU, 4 GB RAM, Ubuntu 24.04, No GPU)
# Memory budget for this container: <= 2.4 GB

# ── Stage 1: Builder (model download + quantization) ─────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY scripts/download_models.py .
RUN python download_models.py --output-dir /build/models

# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /build/models /app/models

COPY app/ /app/app/
COPY litellm_config.yaml /app/litellm_config.yaml
COPY config/ /app/config/
COPY frontend/ /app/frontend/

RUN mkdir -p /app/data/pdfs /app/data/indices

RUN useradd -m -u 1000 analyst && chown -R analyst:analyst /app
USER analyst

ENV OMP_NUM_THREADS=2
ENV ORT_INTRA_OP_NUM_THREADS=2
ENV OMP_WAIT_POLICY=PASSIVE
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--loop", "asyncio", \
     "--log-level", "info", \
     "--access-log"]
