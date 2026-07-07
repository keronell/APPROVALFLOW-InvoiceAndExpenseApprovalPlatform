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
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads((ROOT / "Examples" / "sample-invoices.json").read_text(encoding="utf-8"))
FIXTURES_BY_ID = {f["id"]: f for f in FIXTURES["fixtures"]}

INTAKE = "http://localhost:8001"
DECISION = "http://localhost:8002"
CONFIG = "http://localhost:8003"
PAYMENT = "http://localhost:8004"
APPROVAL = "http://localhost:8005"
REPORTING = "http://localhost:8006"
NOTIFICATION = "http://localhost:8007"
GATEWAY = "http://localhost:8080"

# Services verify needs — deliberately excludes `ollama` (stub model is used).
SERVICES = [
    "redis", "rabbitmq", "placement", "scheduler",
    "config", "config-dapr", "intake", "intake-dapr", "decision", "decision-dapr",
    "payment", "payment-dapr", "approval", "approval-dapr",
    "reporting", "reporting-dapr", "notification", "notification-dapr", "gateway",
]

# Truly terminal submitter-visible statuses. `auto_approved`/`approved` are
# intermediate (payment follows), so waiting on them would race the saga.
TERMINAL = {"rejected", "duplicate", "sent_back", "paid", "payment_failed"}


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


def _post_body(body: dict) -> str:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{INTAKE}/api/invoices", data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)["trackingId"]


def _get_status(tracking_id: str) -> dict:
    with urllib.request.urlopen(f"{INTAKE}/api/invoices/{tracking_id}", timeout=10) as resp:
        return json.load(resp)


def _get_budget(department: str) -> str:
    with urllib.request.urlopen(f"{PAYMENT}/api/budgets/{department}", timeout=10) as resp:
        return json.load(resp)["available"]


def _get_payment(tracking_id: str) -> dict:
    with urllib.request.urlopen(f"{PAYMENT}/api/payments/{tracking_id}", timeout=10) as resp:
        return json.load(resp)


def _get_json(url: str) -> object:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.load(resp)


def _await_status(tracking_id: str, target: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = _get_status(tracking_id)
        if last.get("status") == target:
            return last
        time.sleep(1.5)
    return last


def _approve(tracking_id: str, action: str = "approve") -> None:
    body = json.dumps({"action": action, "approver": "verify@northwind.example"}).encode()
    req = urllib.request.Request(
        f"{APPROVAL}/api/approvals/{tracking_id}/decision",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        assert resp.status == 200, f"approve failed: {resp.status}"


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
    ok = result.get("status") == "paid"
    return ok, f"INV-1001 -> {result.get('status')} (auto-approved and paid, no human)"


def journey_escalate_and_resume() -> tuple[bool, str]:
    """Journey 2 (M11): INV-1003 escalates and the workflow durably pauses.
    We then RESTART the decision service (and its sidecar) between pause and
    resume, approve via the approval API, and the workflow resumes exactly
    where it paused — ending in `paid`."""
    tid = _post_invoice("INV-1003")
    paused = _await_status(tid, "pending_approval")
    if paused.get("status") != "pending_approval":
        return False, f"INV-1003 -> {paused.get('status')} (expected pending_approval)"

    # The approver queue must show the agent's rationale (F4).
    queue = _get_json(f"{APPROVAL}/api/approvals")
    entry = next((i for i in queue if i["escalation"]["trackingId"] == tid), None)
    if entry is None:
        return False, "escalated item not found in the approval queue"
    esc = entry["escalation"]
    if not (esc.get("recommendation") and esc.get("citedRules") is not None
            and esc.get("confidence") is not None):
        return False, "queue item is missing recommendation/confidence/cited rules"

    # Durable pause: survive a restart between pause and resume (M11).
    _compose("restart", "decision")
    _compose("restart", "decision-dapr")
    if not _wait_healthy(f"{DECISION}/healthz"):
        return False, "decision service did not come back after restart"
    time.sleep(6)  # let the workflow runtime reconnect to the sidecar

    _approve(tid)
    result = _await_terminal(tid, timeout=120)
    ok = result.get("status") == "paid"
    return ok, (
        f"INV-1003 escalated -> restart decision -> approved -> "
        f"{result.get('status')} (expected paid)"
    )


def journey_payment_failure_compensation() -> tuple[bool, str]:
    """Journey 4 (canonical, INV-1012): capital hardware escalates, a human
    approves, then the payment is forced to fail — the saga compensates:
    status payment_failed and the budget fully restored (no orphaned
    reservation, no partial payment)."""
    fixture = dict(FIXTURES_BY_ID["INV-1012"])
    fixture["test"] = {"failPayment": True}
    dept = fixture["department"]

    before = _get_budget(dept)
    tid = _post_body(fixture)
    paused = _await_status(tid, "pending_approval")
    if paused.get("status") != "pending_approval":
        return False, f"INV-1012 -> {paused.get('status')} (expected pending_approval)"

    _approve(tid)
    result = _await_terminal(tid, timeout=120)
    after = _get_budget(dept)

    status_ok = result.get("status") == "payment_failed"
    budget_ok = before == after  # reserved then released → no net change
    ok = status_ok and budget_ok
    return ok, (
        f"INV-1012 approved then payment failed -> {result.get('status')}; "
        f"budget {dept} {before}->{after} ({'restored' if budget_ok else 'LEAKED'})"
    )


def guard_concurrent_budget() -> tuple[bool, str]:
    """INV-1014A/B: two $600 items against the $1,000 marketing budget. After
    both are human-approved, exactly ONE is paid; the budget never goes below 0."""
    tid_a = _post_invoice("INV-1014A")
    tid_b = _post_invoice("INV-1014B")
    for tid, name in ((tid_a, "A"), (tid_b, "B")):
        if _await_status(tid, "pending_approval").get("status") != "pending_approval":
            return False, f"INV-1014{name} did not escalate"

    _approve(tid_a)
    _approve(tid_b)
    result_a = _await_terminal(tid_a, timeout=120)
    result_b = _await_terminal(tid_b, timeout=120)
    budget = _get_budget("marketing-2026Q2")

    statuses = sorted([result_a.get("status"), result_b.get("status")])
    ok = statuses == ["paid", "payment_failed"] and float(budget) >= 0
    return ok, (
        f"INV-1014A/B -> {result_a.get('status')}/{result_b.get('status')}; "
        f"marketing budget left: {budget} (exactly one paid, no overspend)"
    )


def guard_no_zero_dodge() -> tuple[bool, str]:
    """The configured ceiling must be > 0 (an escalate-everything posture fails)."""
    thresholds = _get_json(f"{CONFIG}/config/thresholds")
    ceiling = float(thresholds.get("ceiling_usd", 0))
    ok = ceiling > 0
    return ok, f"configured AUTONOMY-CEILING = ${ceiling:g} (must be > 0)"


def check_notification_inbox() -> tuple[bool, str]:
    """M8: the final result is delivered on a notification channel — INV-1001's
    submitter finds a `paid` notification in their inbox (via the gateway),
    without knowing the tracking id."""
    submitter = FIXTURES_BY_ID["INV-1001"]["submitter"]
    inbox = _get_json(
        f"{GATEWAY}/api/notifications?submitter={urllib.parse.quote(submitter)}"
    )
    entry = next(
        (n for n in inbox if n["invoice_id"] == "INV-1001" and n["status"] == "paid"), None
    )
    ok = entry is not None
    return ok, (
        f"inbox({submitter}): {len(inbox)} notification(s); "
        f"INV-1001 paid present: {ok}"
    )


def check_gateway() -> tuple[bool, str]:
    """M6: the gateway is the single entry point — it serves the UI, proxies the
    API (submit + status through :8080), and exposes the dashboard metrics."""
    with urllib.request.urlopen(f"{GATEWAY}/", timeout=10) as resp:
        ui_ok = resp.status == 200 and b"ApprovalFlow" in resp.read()

    fixture = dict(FIXTURES_BY_ID["INV-1017"])  # in-policy hardware, auto-approves
    body = json.dumps(fixture).encode()
    req = urllib.request.Request(
        f"{GATEWAY}/api/invoices", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        tid = json.load(resp)["trackingId"]
    with urllib.request.urlopen(f"{GATEWAY}/api/invoices/{tid}", timeout=10) as resp:
        status_ok = resp.status == 200

    summary = _get_json(f"{GATEWAY}/api/reports/summary")
    metrics_ok = summary.get("submitted", 0) >= 1

    ok = ui_ok and status_ok and metrics_ok
    return ok, (
        f"UI served: {ui_ok}; submit+status proxied: {status_ok}; "
        f"dashboard metrics visible: {metrics_ok} (submitted={summary.get('submitted')})"
    )


def journey_duplicate() -> tuple[bool, str]:
    """Journey 3: INV-1007 is an exact re-submission of INV-1001 (same vendor +
    invoice number + total). INV-1001 was already submitted by Journey 1, so the
    re-submission must short-circuit to `duplicate` at intake — no second
    decision and no second payment (F3/M10)."""
    tid = _post_invoice("INV-1007")
    result = _await_terminal(tid)
    status = result.get("status")
    payment = _get_payment(tid)  # the duplicate never entered payment
    ok = status == "duplicate" and payment.get("status") == "pending"
    return ok, (
        f"INV-1007 -> {status}; payment for the duplicate is "
        f"'{payment.get('status')}' (short-circuited, not paid)"
    )


def guard_second_auto_approve() -> tuple[bool, str]:
    tid = _post_invoice("INV-1016")  # economy taxi, in policy
    result = _await_terminal(tid)
    ok = result.get("status") == "paid"
    return ok, f"INV-1016 -> {result.get('status')} (>=2 no-human auto-approvals)"


def guard_prompt_injection() -> tuple[bool, str]:
    tid = _post_invoice("INV-1013")  # notes say 'Approve me...'; $300 over ceiling
    result = _await_terminal(tid)
    status = result.get("status")
    ok = status not in ("auto_approved", "paid")
    return ok, f"INV-1013 'approve me' -> {status} (must NOT auto-approve)"


CHECKS = [
    # Journey 1 posts INV-1001; Journey 3 then re-submits it as INV-1007.
    ("Journey 1: auto-approve + pay, no human", journey_auto_approve),
    ("Journey 2: escalate, restart, resume (durable HITL)", journey_escalate_and_resume),
    ("Journey 3: duplicate paid once, resubmit short-circuited", journey_duplicate),
    (
        "Journey 4: approve then payment failure + compensation",
        journey_payment_failure_compensation,
    ),
    ("Guard: >=2 no-human auto-approvals", guard_second_auto_approve),
    ("Guard: 'approve me' note does not flip", guard_prompt_injection),
    ("Guard: concurrent approvals never overspend (INV-1014A/B)", guard_concurrent_budget),
    ("Guard: no $0 dodge (ceiling > 0)", guard_no_zero_dodge),
    ("Gateway: single entry point serves UI + API + metrics", check_gateway),
    ("M8: final outcome delivered to the submitter's inbox", check_notification_inbox),
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
                          ("decision", f"{DECISION}/healthz"),
                          ("config", f"{CONFIG}/healthz"),
                          ("payment", f"{PAYMENT}/healthz"),
                          ("approval", f"{APPROVAL}/healthz"),
                          ("reporting", f"{REPORTING}/healthz"),
                          ("notification", f"{NOTIFICATION}/healthz"),
                          ("gateway", f"{GATEWAY}/healthz")):
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
