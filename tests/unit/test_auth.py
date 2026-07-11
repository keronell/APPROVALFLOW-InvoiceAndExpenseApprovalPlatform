"""Unit tests for the self-signed JWT auth module (N1)."""

from __future__ import annotations

import pytest
from approvalflow_shared.auth import (
    AuthError,
    auth_router,
    require_role,
    sign_token,
    verify_token,
)
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


def test_sign_verify_roundtrip() -> None:
    token = sign_token("alice@northwind.example", "submitter")
    claims = verify_token(token)
    assert claims["sub"] == "alice@northwind.example"
    assert claims["role"] == "submitter"


def test_unknown_role_rejected_at_signing() -> None:
    with pytest.raises(ValueError):
        sign_token("mallory", "superuser")


def test_expired_token_rejected() -> None:
    token = sign_token("alice", "submitter", ttl_seconds=-1)
    with pytest.raises(AuthError, match="expired"):
        verify_token(token)


def test_tampered_payload_rejected() -> None:
    header, payload, signature = sign_token("alice", "submitter").split(".")
    other_payload = sign_token("alice", "admin").split(".")[1]  # forge role upgrade
    with pytest.raises(AuthError, match="signature"):
        verify_token(f"{header}.{other_payload}.{signature}")


def test_wrong_secret_rejected() -> None:
    token = sign_token("alice", "submitter", secret=b"other-secret")
    with pytest.raises(AuthError, match="signature"):
        verify_token(token)


def test_malformed_token_rejected() -> None:
    with pytest.raises(AuthError, match="malformed"):
        verify_token("not-a-jwt")


# --------------------------------------------------------------------------- #
# require_role() as a live FastAPI dependency
# --------------------------------------------------------------------------- #
def _guarded_app() -> TestClient:
    app = FastAPI()
    app.include_router(auth_router())

    @app.get("/anyone", dependencies=[Depends(require_role())])
    async def anyone() -> dict:
        return {"ok": True}

    @app.get("/approvers-only", dependencies=[Depends(require_role("approver"))])
    async def approvers_only() -> dict:
        return {"ok": True}

    return TestClient(app)


def _bearer(role: str) -> dict:
    return {"Authorization": f"Bearer {sign_token('test@northwind.example', role)}"}


def test_missing_token_is_401() -> None:
    client = _guarded_app()
    assert client.get("/anyone").status_code == 401
    assert client.get("/approvers-only").status_code == 401


def test_invalid_token_is_401() -> None:
    client = _guarded_app()
    resp = client.get("/anyone", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401


def test_any_valid_role_passes_unrestricted_gate() -> None:
    client = _guarded_app()
    for role in ("submitter", "approver", "admin"):
        assert client.get("/anyone", headers=_bearer(role)).status_code == 200


def test_wrong_role_is_403_and_admin_passes_everything() -> None:
    client = _guarded_app()
    assert client.get("/approvers-only", headers=_bearer("submitter")).status_code == 403
    assert client.get("/approvers-only", headers=_bearer("approver")).status_code == 200
    assert client.get("/approvers-only", headers=_bearer("admin")).status_code == 200


def test_token_endpoint_issues_working_token() -> None:
    client = _guarded_app()
    issued = client.post(
        "/api/auth/token", json={"user": "amy@northwind.example", "role": "approver"}
    )
    assert issued.status_code == 200
    token = issued.json()["token"]
    resp = client.get("/approvers-only", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_token_endpoint_rejects_unknown_role() -> None:
    client = _guarded_app()
    resp = client.post("/api/auth/token", json={"user": "mallory", "role": "superuser"})
    assert resp.status_code == 422
