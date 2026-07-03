"""Data contracts shared across services.

The submission schema mirrors ``Examples/sample-invoices.json`` (camelCase on
the wire, snake_case in code). Fixture-only fields such as ``expected`` and
``scenario`` are ignored so fixtures can be posted as-is by tests and the
verification command.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ExpenseCategory(StrEnum):
    """Expense categories named by policy.md sections 1-4 (+ catch-all)."""

    MEALS = "meals"
    TRAVEL = "travel"
    SAAS = "saas"
    HARDWARE = "hardware"
    OTHER = "other"


class Route(StrEnum):
    """Final deterministic-router decision (matches ``expected.route`` labels)."""

    AUTO_APPROVE = "auto_approve"
    HUMAN_REVIEW = "human_review"
    REJECT = "reject"
    DUPLICATE = "duplicate"


class ItemStatus(StrEnum):
    """Submitter-visible lifecycle of a submission (F2)."""

    RECEIVED = "received"
    PROCESSING = "processing"
    PENDING_APPROVAL = "pending_approval"
    AUTO_APPROVED = "auto_approved"
    APPROVED = "approved"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    SENT_BACK = "sent_back"
    PAYING = "paying"
    PAID = "paid"
    PAYMENT_FAILED = "payment_failed"


class Topics:
    """RabbitMQ pub/sub topics (via Dapr)."""

    ITEM_SUBMITTED = "item.submitted"
    DECISION_MADE = "decision.made"
    ESCALATION_REQUESTED = "escalation.requested"
    ESCALATION_RESOLVED = "escalation.resolved"
    PAYMENT_REQUESTED = "payment.requested"
    PAYMENT_COMPLETED = "payment.completed"
    PAYMENT_FAILED = "payment.failed"
    ITEM_STATUS_CHANGED = "item.status-changed"

    PUBSUB_NAME = "approvalflow-pubsub"


class _CamelModel(BaseModel):
    """Base model: camelCase aliases on the wire, snake_case in code."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
    )


class LineItem(_CamelModel):
    description: str
    quantity: Decimal
    unit_price: Decimal


class TestControls(_CamelModel):
    """Documented test hooks — never business logic. Used by tools/verify.py."""

    fail_payment: bool = False


class InvoiceSubmission(_CamelModel):
    """An invoice/expense as submitted (mirrors the shipped fixtures)."""

    id: str = Field(min_length=1, description="Client-side invoice id, e.g. INV-1001")
    submitter: str
    department: str
    vendor: str
    vendor_known: bool
    invoice_number: str
    currency: str = "USD"
    category: ExpenseCategory
    attendees: int | None = None
    line_items: list[LineItem] = Field(min_length=0)
    tax_amount: Decimal = Decimal("0")
    total: Decimal
    receipt_present: bool = False
    date: dt.date
    notes: str | None = None
    test: TestControls | None = None

    @property
    def fingerprint(self) -> str:
        """GLOBAL-DUP identity: same vendor + invoiceNumber + total."""
        return f"{self.vendor.lower()}|{self.invoice_number.lower()}|{self.total}"


class SubmissionAccepted(_CamelModel):
    """202 response body from intake (F1)."""

    tracking_id: str
    status: ItemStatus
    status_url: str


class ItemSubmitted(_CamelModel):
    """Event published by intake when a new item enters the pipeline."""

    tracking_id: str
    correlation_id: str
    invoice: InvoiceSubmission


class StatusChanged(_CamelModel):
    """Event published whenever an item's submitter-visible status changes."""

    tracking_id: str
    correlation_id: str
    status: ItemStatus
    reason: str
