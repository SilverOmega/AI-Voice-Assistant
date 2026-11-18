FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Runs as an unprivileged user; docker-compose adds a read-only filesystem, a small /tmp,
# no capabilities, process and memory limits, and a network with no way out.
RUN useradd --no-create-home --uid 1001 runner
USER runner

EXPOSE 8004
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8004/health', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
