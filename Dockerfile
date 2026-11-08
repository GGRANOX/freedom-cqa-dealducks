# CQA · Freedom Broker — образ для Cloud Run (и любого Docker-хостинга)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8080
WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY backend ./backend
COPY static ./static
COPY assets ./assets
COPY scripts ./scripts
COPY docs ./docs

# непривилегированный пользователь; SQLite (если нет DATABASE_URL) пишет в /app/data
RUN useradd -m -u 10001 app && mkdir -p /app/data && chown -R app /app
USER app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/api/health')"
CMD ["sh", "-c", "uvicorn backend.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 75"]
