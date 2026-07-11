"""Self-signed JWT authN/authZ with roles (N1).

HS256 JWTs signed and verified with the stdlib only (``hmac``/``hashlib``) —
no new dependency, which keeps the decision image's pinned resolution intact.
The shared secret comes from ``AUTH_SECRET`` (``.env``; dev default matches
``.env.example``). Roles are ``submitter`` / ``approver`` / ``admin``; admin
passes every gate.

Trust boundary: only *user-facing* endpoints (reached through the gateway)
carry ``require_role`` — Dapr-internal surfaces (``/dapr/subscribe``,
``/events/*``, service-invocation targets, ``/healthz``) stay open, as they
are only reachable from the sidecar/compose network, never via the gateway.
The token issuer is a dev-grade stub (any caller may request any role, which
is what the assignment's "self-signed JWT is fine" allows); production would
swap it for a real identity provider without touching ``require_role``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

ROLES = ("submitter", "approver", "admin")
DEFAULT_TTL_SECONDS = 8 * 3600

_DEV_SECRET = "approvalflow-dev-auth-secret"


class AuthError(Exception):
    """Token missing, malformed, expired, or badly signed."""


def _secret() -> bytes:
    return os.environ.get("AUTH_SECRET", _DEV_SECRET).encode()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _signature(signing_input: bytes, secret: bytes) -> str:
    return _b64url(hmac.new(secret, signing_input, hashlib.sha256).digest())


def sign_token(
    subject: str,
    role: str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    secret: bytes | None = None,
) -> str:
    """Mint a self-signed HS256 JWT for ``subject`` with ``role``."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {ROLES}")
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    now = int(time.time())
    payload = _b64url(
        json.dumps({"sub": subject, "role": role, "iat": now, "exp": now + ttl_seconds}).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    return f"{header}.{payload}.{_signature(signing_input, secret or _secret())}"


def verify_token(token: str, *, secret: bytes | None = None) -> dict:
    """Verify signature + expiry and return the claims. Raises ``AuthError``."""
    try:
        header_seg, payload_seg, signature_seg = token.split(".")
    except ValueError as exc:
        raise AuthError("malformed token") from exc

    signing_input = f"{header_seg}.{payload_seg}".encode()
    expected = _signature(signing_input, secret or _secret())
    if not hmac.compare_digest(expected, signature_seg):
        raise AuthError("bad signature")

    try:
        claims = json.loads(_b64url_decode(payload_seg))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError("malformed claims") from exc
    if not isinstance(claims, dict):
        raise AuthError("malformed claims")
    if int(claims.get("exp", 0)) < time.time():
        raise AuthError("token expired")
    return claims


def require_role(*roles: str):
    """FastAPI dependency: a valid Bearer token, optionally restricted to roles.

    ``require_role()`` accepts any authenticated user; ``require_role("approver")``
    accepts approvers and admins (admin passes every gate). 401 for a missing or
    invalid token, 403 for a valid token with the wrong role.
    """

    async def dependency(request: Request) -> dict:
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        try:
            claims = verify_token(authorization.removeprefix("Bearer "))
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        role = claims.get("role")
        if roles and role != "admin" and role not in roles:
            raise HTTPException(
                status_code=403, detail=f"Requires role {' or '.join(roles)} (have {role})"
            )
        request.state.auth = claims
        return claims

    return dependency


# --------------------------------------------------------------------------- #
# Dev token issuer (self-signed): mounted on the intake service, proxied by the
# gateway at /api/auth/token. No user store — any caller may request any role.
# --------------------------------------------------------------------------- #
class TokenRequest(BaseModel):
    user: str
    role: Literal["submitter", "approver", "admin"]


class TokenResponse(BaseModel):
    token: str
    user: str
    role: str
    expiresInSeconds: int


def auth_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/auth/token", response_model=TokenResponse, tags=["auth"])
    async def issue_token(body: TokenRequest) -> TokenResponse:
        """Issue a self-signed role-scoped JWT (N1 dev issuer)."""
        return TokenResponse(
            token=sign_token(body.user, body.role),
            user=body.user,
            role=body.role,
            expiresInSeconds=DEFAULT_TTL_SECONDS,
        )

    return router
