FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY *.py ./
COPY templates ./templates
COPY migrations ./migrations
COPY seed ./seed

# Run as an unprivileged user; the SQLite database lives in /app/data (a volume).
RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown app:app /app/data
USER app

ENV DATABASE_PATH=/app/data/news.db \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

# uvicorn handles SIGTERM: the lifespan stops the workers and Telegram polling cleanly.
STOPSIGNAL SIGTERM
CMD ["python", "main.py"]
