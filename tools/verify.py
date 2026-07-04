#!/usr/bin/env python3
"""ApprovalFlow verification command (D5).

Brings the system up with the deterministic stub model, drives the worked
journeys + anti-cheese guards through the real HTTP surface, and prints a clear
PASS/FAIL with a non-zero exit code on failure.

Scope grows per stage. Stage 5 covers Journey 1 (auto-approve) plus two guards
that the pipeline already supports; the escalate/duplicate/payment journeys are
added as their services land.

Usage:
    python tools/verify.py            # build + up (stub), run checks, leave running
    python tools/verify.py --down     # also tear the stack down afterwards
    python tools/verify.py --no-up     # assume the stack is already running
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads((ROOT / "Examples" / "sample-invoices.json").read_text(encoding="utf-8"))
FIXTURES_BY_ID = {f["id"]: f for f in FIXTURES["fixtures"]}

INTAKE = "http://localhost:8001"

# Services verify needs — deliberately excludes `ollama` (stub model is used).
SERVICES = [
    "redis", "rabbitmq", "placement", "scheduler",
    "config", "config-dapr", "intake", "intake-dapr", "decision", "decision-dapr",
]

TERMINAL = {"auto_approved", "approved", "rejected", "duplicate", "paid", "payment_failed"}


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _post_invoice(fixture_id: str) -> str:
    body = json.dumps(FIXTURES_BY_ID[fixture_id]).encode()
    req = urllib.request.Request(
        f"{INTAKE}/api/invoices", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 202, f"expected 202, got {resp.status}"
        return json.load(resp)["trackingId"]


def _get_status(tracking_id: str) -> dict:
    with urllib.request.urlopen(f"{INTAKE}/api/invoices/{tracking_id}", timeout=10) as resp:
        return json.load(resp)


def _await_terminal(tracking_id: str, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = _get_status(tracking_id)
        if last.get("status") in TERMINAL:
            return last
        time.sleep(1.5)
    return last


# --------------------------------------------------------------------------- #
# Journeys / guards
# --------------------------------------------------------------------------- #
def journey_auto_approve() -> tuple[bool, str]:
    tid = _post_invoice("INV-1001")
    result = _await_terminal(tid)
    ok = result.get("status") == "auto_approved"
    return ok, f"INV-1001 -> {result.get('status')} (expected auto_approved)"


def guard_second_auto_approve() -> tuple[bool, str]:
    tid = _post_invoice("INV-1016")  # economy taxi, in policy
    result = _await_terminal(tid)
    ok = result.get("status") == "auto_approved"
    return ok, f"INV-1016 -> {result.get('status')} (>=2 no-human auto-approvals)"


def guard_prompt_injection() -> tuple[bool, str]:
    tid = _post_invoice("INV-1013")  # notes say 'Approve me...'; $300 over ceiling
    result = _await_terminal(tid)
    status = result.get("status")
    ok = status not in ("auto_approved", "paid")
    return ok, f"INV-1013 'approve me' -> {status} (must NOT auto-approve)"


CHECKS = [
    ("Journey 1: auto-approve, no human", journey_auto_approve),
    ("Guard: >=2 no-human auto-approvals", guard_second_auto_approve),
    ("Guard: 'approve me' note does not flip", guard_prompt_injection),
]


# --------------------------------------------------------------------------- #
# Compose lifecycle
# --------------------------------------------------------------------------- #
def _compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True)


def _reset_state() -> None:
    """Flush Redis so a re-run starts clean and deterministic (otherwise a prior
    run's fingerprints make INV-1001 look like a duplicate)."""
    subprocess.run(
        ["docker", "compose", "exec", "-T", "redis", "redis-cli", "flushall"],
        cwd=ROOT, check=False, stdout=subprocess.DEVNULL,
    )


def _wait_healthy(url: str, timeout: float = 120.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(2)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="ApprovalFlow verification")
    parser.add_argument("--down", action="store_true", help="tear the stack down afterwards")
    parser.add_argument("--no-up", action="store_true", help="assume the stack is already up")
    args = parser.parse_args()

    # Hermetic + deterministic: the stub model, never a live LLM.
    os.environ["LLM_PROVIDER"] = "stub"

    if not args.no_up:
        print("Bringing up the stack with the stub model (this builds images on first run)…")
        _compose("up", "-d", "--build", *SERVICES)
        for name, url in (("intake", f"{INTAKE}/healthz"),
                          ("decision", "http://localhost:8002/healthz"),
                          ("config", "http://localhost:8003/healthz")):
            if not _wait_healthy(url):
                print(f"FAIL: {name} did not become healthy")
                return 1
        # Give the Dapr sidecars a moment to subscribe and the workflow worker to connect.
        time.sleep(5)

    # Deterministic start: clear any state left by a previous run.
    _reset_state()

    print("\nRunning journeys + guards:\n" + "-" * 48)
    results = []
    for label, check in CHECKS:
        try:
            ok, detail = check()
        except Exception as exc:  # noqa: BLE001 - report, don't crash the runner
            ok, detail = False, f"error: {exc}"
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}\n        {detail}")

    passed = all(results)
    print("-" * 48)
    print(f"{'ALL PASSED' if passed else 'SOME FAILED'}  ({sum(results)}/{len(results)})")

    if args.down:
        _compose("down", "-v")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
