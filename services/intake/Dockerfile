# Build context is the repo root (services share the local approvalflow-shared).
FROM python:3.12-slim

WORKDIR /app

# Install shared library first (changes least often → better layer caching).
COPY services/shared /app/services/shared
RUN pip install --no-cache-dir ./services/shared

COPY services/intake /app/services/intake
RUN pip install --no-cache-dir ./services/intake

EXPOSE 8000
CMD ["uvicorn", "approvalflow_intake.app:app", "--host", "0.0.0.0", "--port", "8000"]
