# Runs on Raspberry Pi 4/5 (64-bit OS, linux/arm64). Also builds on amd64 for dev.
# Targets: gateway (default, last stage) and dashboard (adds Streamlit).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 1000 appuser && mkdir -p /app/logs && chown appuser /app/logs


# Streamlit dashboard (compose service "dashboard"). Heavy deps (pandas, pyarrow) stay out of the gateway image.
FROM base AS dashboard

COPY requirements-dashboard.txt .
RUN pip install --no-cache-dir -r requirements-dashboard.txt

# Secrets (.env) are NOT copied; see .dockerignore. They come in at runtime via env_file.
COPY . .
USER appuser
ENV PYTHONPATH=/app
EXPOSE 8501
CMD ["streamlit", "run", "app/dashboard/main.py", \
     "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", \
     "--browser.gatherUsageStats=false"]


# Gateway service + mock PLC. Last stage = default target of a plain `docker build .`
FROM base AS gateway

# Secrets (.env) are NOT copied; see .dockerignore. They come in at runtime via env_file.
COPY . .
USER appuser

CMD ["python", "-m", "app"]
