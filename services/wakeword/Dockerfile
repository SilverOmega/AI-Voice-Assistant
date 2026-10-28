FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake the small (~10 MB) pretrained wake-word and feature models into the image.
ARG WAKEWORD_MODELS="hey_jarvis alexa hey_mycroft"
RUN python -c "import sys; from openwakeword.utils import download_models; download_models(sys.argv[1:])" $WAKEWORD_MODELS

COPY app ./app
COPY scripts ./scripts

RUN useradd --create-home --uid 1000 app && mkdir -p /app/models/wakeword && chown -R app:app /app/models
USER app
ENV WAKEWORD_DOWNLOAD=false

EXPOSE 8003
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8003/health', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
