FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# Whisper and Piper models are downloaded on first start into the /models volume.
RUN useradd --create-home --uid 1000 app && mkdir -p /models && chown app:app /models
USER app
ENV HF_HOME=/models/huggingface \
    PIPER_DATA_DIR=/models/piper

EXPOSE 8002
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/health', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
