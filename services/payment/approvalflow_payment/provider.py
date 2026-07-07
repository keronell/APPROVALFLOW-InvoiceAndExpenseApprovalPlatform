"""Fake payment provider.

Deterministic and offline. Fails when the submission carries the documented
test hook ``test.failPayment`` — this is how the verification command exercises
the compensation path (Journey 4) without a real payment gateway.

The provider takes an **idempotency key** and derives the payment id from it,
modeling how a real gateway deduplicates retried charges: calling it twice with
the same key yields the same payment, never a second charge. The saga records a
durable "attempting" marker with this key *before* calling the provider, so a
crash between the provider call and the state write cannot double-charge on
retry.
"""

from __future__ import annotations

import hashlib

from approvalflow_shared.contracts import InvoiceSubmission


class PaymentDeclined(RuntimeError):
    """Raised by the provider to fail a payment (triggers saga compensation)."""


def execute_payment(invoice: InvoiceSubmission, usd_amount: str, idempotency_key: str) -> str:
    if invoice.test is not None and invoice.test.fail_payment:
        raise PaymentDeclined(f"payment declined for {invoice.id} (injected failure)")
    digest = hashlib.sha256(idempotency_key.encode()).hexdigest()[:12]
    return f"pay_{digest}"
