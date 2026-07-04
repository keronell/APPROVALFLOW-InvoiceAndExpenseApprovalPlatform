"""Fake payment provider.

Deterministic and offline. Fails when the submission carries the documented
test hook ``test.failPayment`` — this is how the verification command exercises
the compensation path (Journey 4) without a real payment gateway.
"""

from __future__ import annotations

import uuid

from approvalflow_shared.contracts import InvoiceSubmission


class PaymentDeclined(RuntimeError):
    """Raised by the provider to fail a payment (triggers saga compensation)."""


def execute_payment(invoice: InvoiceSubmission, usd_amount: str) -> str:
    if invoice.test is not None and invoice.test.fail_payment:
        raise PaymentDeclined(f"payment declined for {invoice.id} (injected failure)")
    return f"pay_{uuid.uuid4().hex[:12]}"
