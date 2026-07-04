"""Contracts must parse every shipped fixture as-is (camelCase wire format)."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from approvalflow_shared.contracts import InvoiceSubmission, Route

FIXTURES_PATH = Path(__file__).parents[2] / "Examples" / "sample-invoices.json"


def load_fixtures() -> list[dict]:
    return json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))["fixtures"]


@pytest.mark.parametrize("fixture", load_fixtures(), ids=lambda f: f["id"])
def test_every_shipped_fixture_parses(fixture: dict) -> None:
    invoice = InvoiceSubmission.model_validate(fixture)
    assert invoice.id == fixture["id"]
    assert invoice.total == Decimal(str(fixture["total"]))
    # Every label in the fixture set must be a route our system knows.
    assert Route(fixture["expected"]["route"])


def test_fingerprint_matches_global_dup_identity() -> None:
    """GLOBAL-DUP: same vendor + invoiceNumber + total ⇒ same fingerprint."""
    fixtures = {f["id"]: f for f in load_fixtures()}
    original = InvoiceSubmission.model_validate(fixtures["INV-1001"])
    duplicate = InvoiceSubmission.model_validate(fixtures["INV-1007"])
    assert original.fingerprint == duplicate.fingerprint


def test_fingerprint_differs_when_total_differs() -> None:
    fixtures = {f["id"]: f for f in load_fixtures()}
    a = InvoiceSubmission.model_validate(fixtures["INV-1014A"])
    b = InvoiceSubmission.model_validate(fixtures["INV-1014B"])
    assert a.fingerprint != b.fingerprint


def test_camel_case_round_trip() -> None:
    fixtures = {f["id"]: f for f in load_fixtures()}
    invoice = InvoiceSubmission.model_validate(fixtures["INV-1001"])
    wire = invoice.model_dump(by_alias=True, mode="json")
    assert "invoiceNumber" in wire
    assert "lineItems" in wire
    assert InvoiceSubmission.model_validate(wire) == invoice
