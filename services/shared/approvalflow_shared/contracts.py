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
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)


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
    attendees: int | None = Field(default=None, ge=1)
    line_items: list[LineItem] = Field(min_length=0)
    # Money fields are constrained non-negative / positive: a negative total
    # would otherwise sail under every ceiling/receipt check and a negative
    # reservation would INCREASE the department budget.
    tax_amount: Decimal = Field(default=Decimal("0"), ge=0)
    total: Decimal = Field(gt=0)
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


class DecisionMade(_CamelModel):
    """Event published by the decision service when a route is decided."""

    tracking_id: str
    correlation_id: str
    invoice_id: str
    route: str
    status: ItemStatus
    reason: str
    cited_rules: list[str] = Field(default_factory=list)
    usd_amount: str


class PaymentRequested(_CamelModel):
    """Event asking the payment service to pay an approved item."""

    tracking_id: str
    correlation_id: str
    invoice: InvoiceSubmission
    usd_amount: str


class ApprovalAction(StrEnum):
    """What an approver can do with an escalated item — one action (F5)."""

    APPROVE = "approve"
    REJECT = "reject"
    SEND_BACK = "send_back"


class EscalationRequested(_CamelModel):
    """Event carrying everything the approver queue needs to show (F4)."""

    tracking_id: str
    correlation_id: str
    invoice_id: str
    submitter: str
    department: str
    vendor: str
    usd_amount: str
    reason: str
    cited_rules: list[str] = Field(default_factory=list)
    recommendation: str
    confidence: float
    risk: str = "unknown"


class HumanDecision(_CamelModel):
    """The approver's decision, raised into the paused decision workflow (F5/M11)."""

    action: ApprovalAction
    approver: str = "approver@northwind.example"
    note: str = ""


class EscalationResolved(_CamelModel):
    """Event announcing a human decision (consumed by reporting)."""

    tracking_id: str
    correlation_id: str
    action: ApprovalAction
    usd_amount: str
    approver: str = ""
