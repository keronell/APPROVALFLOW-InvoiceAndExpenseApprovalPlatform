"""Provider idempotency: same key → same payment id, never a second charge."""

import pytest
from approvalflow_payment.provider import PaymentDeclined, execute_payment
from approvalflow_shared.contracts import InvoiceSubmission


@pytest.fixture
def invoice(fixtures_by_id) -> InvoiceSubmission:
    return InvoiceSubmission.model_validate(fixtures_by_id["INV-1001"])


def test_same_idempotency_key_yields_same_payment_id(invoice):
    first = execute_payment(invoice, "42.00", idempotency_key="tid-1")
    retried = execute_payment(invoice, "42.00", idempotency_key="tid-1")
    assert first == retried  # a crash-and-retry cannot double charge


def test_different_keys_yield_different_payments(invoice):
    a = execute_payment(invoice, "42.00", idempotency_key="tid-1")
    b = execute_payment(invoice, "42.00", idempotency_key="tid-2")
    assert a != b


def test_fail_payment_hook_still_declines(fixtures_by_id):
    fixture = {**fixtures_by_id["INV-1001"], "test": {"failPayment": True}}
    invoice = InvoiceSubmission.model_validate(fixture)
    with pytest.raises(PaymentDeclined):
        execute_payment(invoice, "42.00", idempotency_key="tid-1")
