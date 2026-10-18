# One image for both roles:
#   API:    default CMD (uvicorn)
#   Worker: python -m app.worker
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /code

# libpq for psycopg, ffmpeg for conversions, curl for healthchecks.
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY scripts ./scripts

RUN useradd -m appuser && mkdir -p /code/data/results && chown -R appuser:appuser /code
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/health" || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
