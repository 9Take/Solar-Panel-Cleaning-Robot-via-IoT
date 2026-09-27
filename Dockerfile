# Runs on Raspberry Pi 4/5 (64-bit OS, linux/arm64). Also builds on amd64 for dev.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Secrets (.env) are NOT copied; see .dockerignore. They come in at runtime via env_file.
COPY . .

RUN useradd --create-home --uid 1000 appuser && mkdir -p /app/logs && chown appuser /app/logs
USER appuser

CMD ["python", "-m", "app"]
