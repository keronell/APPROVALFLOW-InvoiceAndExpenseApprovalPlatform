"""Structured JSON logging with the correlation id on every line (M14)."""

from __future__ import annotations

import logging
import sys

import structlog

from .correlation import get_correlation_id


def _add_correlation_id(_logger, _method_name, event_dict: dict) -> dict:
    event_dict["correlation_id"] = get_correlation_id()
    return event_dict


def configure_logging(service_name: str, *, level: int = logging.INFO) -> None:
    """Configure structlog to emit one JSON object per line to stdout."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_correlation_id,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service_name)


def get_logger(**initial_values) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(**initial_values)
