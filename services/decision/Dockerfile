# Build context is the repo root (services share the local approvalflow-shared).
FROM python:3.12-slim

WORKDIR /app

# Pinned resolution first (small, cache-friendly): the agent-framework
# meta package pulls agent-framework-core[all], whose beta/rc extras make an
# unconstrained resolve backtrack for tens of minutes and then fail (exit 2).
# The constraints mirror the known-good .venv so pip resolves deterministically.
COPY services/decision/constraints.txt /app/constraints.txt

COPY services/shared /app/services/shared
RUN pip install --no-cache-dir -c /app/constraints.txt ./services/shared

COPY services/decision /app/services/decision
RUN pip install --no-cache-dir -c /app/constraints.txt ./services/decision

EXPOSE 8000
CMD ["uvicorn", "approvalflow_decision.service.app:app", "--host", "0.0.0.0", "--port", "8000"]
