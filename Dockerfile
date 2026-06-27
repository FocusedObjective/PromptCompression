FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/cache/huggingface \
    HF_HUB_CACHE=/cache/huggingface/hub \
    TRANSFORMERS_CACHE=/cache/huggingface/hub

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch==2.12.1+cpu" \
    && pip install --no-cache-dir -r requirements.txt

ARG COMPRESSOR_MODEL=microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank
ENV COMPRESSOR_MODEL=${COMPRESSOR_MODEL}
RUN mkdir -p /cache/huggingface/hub \
    && python -c "import os; from huggingface_hub import snapshot_download; snapshot_download(os.environ['COMPRESSOR_MODEL'], cache_dir=os.environ['HF_HUB_CACHE'])"
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

RUN adduser --disabled-password --gecos "" appuser \
    && chown -R appuser:appuser /app /cache

COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser data ./data
COPY --chown=appuser:appuser models ./models

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import json, os, urllib.request; port = os.getenv('PORT', '8080'); json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3))"

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
