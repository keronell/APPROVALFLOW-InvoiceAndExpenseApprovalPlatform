"""FastAPI app factory: every service gets the same cross-cutting baseline —
structured logging, correlation-id middleware, and a health check (M14/M15).
"""

from __future__ import annotations

from fastapi import FastAPI

from .correlation import CorrelationIdMiddleware
from .logging import configure_logging, get_logger


def create_app(service_name: str, **fastapi_kwargs) -> FastAPI:
    configure_logging(service_name)
    log = get_logger()

    app = FastAPI(title=f"ApprovalFlow — {service_name}", **fastapi_kwargs)
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict:
        return {"status": "ok", "service": service_name}

    log.info("service_configured", service_name=service_name)
    return app
