"""App-factory baseline: health check + correlation-id propagation."""

from approvalflow_shared.correlation import CORRELATION_HEADER
from approvalflow_shared.web import create_app
from fastapi.testclient import TestClient


def make_client() -> TestClient:
    return TestClient(create_app("test-service"))


def test_healthz_reports_ok() -> None:
    response = make_client().get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "test-service"}


def test_incoming_correlation_id_is_echoed() -> None:
    response = make_client().get("/healthz", headers={CORRELATION_HEADER: "corr-123"})
    assert response.headers[CORRELATION_HEADER] == "corr-123"


def test_missing_correlation_id_is_minted() -> None:
    response = make_client().get("/healthz")
    minted = response.headers[CORRELATION_HEADER]
    assert minted and len(minted) == 32  # uuid4 hex
