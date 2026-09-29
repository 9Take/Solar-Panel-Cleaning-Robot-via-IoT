# Dashboard mockup (Streamlit). Build context is the repo root:
#   docker compose -f dashboard/docker-compose.yml up -d --build
# Runs on Raspberry Pi 4/5 (64-bit OS, linux/arm64). Also builds on amd64 for dev.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY dashboard/requirements.txt dashboard/requirements.txt
RUN pip install --no-cache-dir -r dashboard/requirements.txt

# Only the dashboard code; config/ and logs/ are mounted by docker-compose.yml.
COPY dashboard/app.py dashboard/app.py

# Same uid as the gateway image so both can write the shared SQLite files in logs/.
RUN useradd --create-home --uid 1000 appuser && mkdir -p /app/logs && chown appuser /app/logs
USER appuser

EXPOSE 8501
CMD ["streamlit", "run", "dashboard/app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
