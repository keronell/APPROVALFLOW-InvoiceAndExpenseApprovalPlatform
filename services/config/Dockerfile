# Build context is the repo root (services share the local approvalflow-shared).
FROM python:3.12-slim

WORKDIR /app

COPY services/shared /app/services/shared
RUN pip install --no-cache-dir ./services/shared

COPY services/config /app/services/config
RUN pip install --no-cache-dir ./services/config

EXPOSE 8000
CMD ["uvicorn", "approvalflow_config.app:app", "--host", "0.0.0.0", "--port", "8000"]
