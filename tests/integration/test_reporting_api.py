"""Reporting service: metrics aggregation + audit trail (F8/F9)."""

import pytest
from approvalflow_reporting.app import build_app
from approvalflow_reporting.service import InMemoryReportStore, ReportingService
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    return TestClient(build_app(ReportingService(InMemoryReportStore())))


def _event(client, topic: str, tid: str, **fields):
    resp = client.post(f"/events/{topic}", json={"data": {"trackingId": tid, **fields}})
    assert resp.status_code == 200


def test_dashboard_aggregates_the_pipeline(client):
    _event(client, "item.submitted", "t1")
    _event(client, "item.submitted", "t2")
    _event(client, "decision.made", "t1", route="auto_approve", usdAmount="42.00")
    _event(client, "decision.made", "t2", route="human_review", usdAmount="1820.00")
    _event(client, "escalation.resolved", "t2", action="approve", usdAmount="1820.00")
    _event(client, "payment.completed", "t1")
    _event(client, "payment.completed", "t2")

    summary = client.get("/api/reports/summary").json()
    assert summary["submitted"] == 2
    assert summary["auto_approved"] == 1
    assert summary["escalated"] == 1
    assert summary["human_approved"] == 1
    assert summary["paid"] == 2
    assert summary["auto_approved_usd"] == "42.00"
    assert summary["human_approved_usd"] == "1820.00"
    assert summary["auto_approval_rate"] == 0.5


def test_audit_trail_collects_every_event_for_one_item(client):
    """F9: the complete decision trail, linked by one correlation id."""
    _event(client, "item.submitted", "t1")
    _event(client, "decision.made", "t1", route="human_review", usdAmount="9500")
    _event(client, "escalation.requested", "t1", reason="HW-02")
    _event(client, "escalation.resolved", "t1", action="approve", usdAmount="9500")
    _event(client, "payment.failed", "t1")

    trail = client.get("/api/audit/t1").json()
    topics = [entry["topic"] for entry in trail]
    assert topics == [
        "item.submitted",
        "decision.made",
        "escalation.requested",
        "escalation.resolved",
        "payment.failed",
    ]


def test_unknown_item_has_empty_trail(client):
    assert client.get("/api/audit/nope").json() == []


def test_redelivered_event_does_not_double_count(client):
    """M10: at-least-once delivery must not inflate counters or duplicate
    audit entries — the (topic, tracking id) claim drops redeliveries."""
    _event(client, "item.submitted", "t1")
    _event(client, "item.submitted", "t1")  # pub/sub redelivery
    _event(client, "decision.made", "t1", route="auto_approve", usdAmount="42.00")
    _event(client, "decision.made", "t1", route="auto_approve", usdAmount="42.00")

    summary = client.get("/api/reports/summary").json()
    assert summary["submitted"] == 1
    assert summary["auto_approved"] == 1
    assert summary["auto_approved_usd"] == "42.00"  # not 84.00

    trail = client.get("/api/audit/t1").json()
    assert [e["topic"] for e in trail] == ["item.submitted", "decision.made"]
