"""Domain models for the decisioning core."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import IntEnum, StrEnum
from typing import Annotated

from approvalflow_shared.contracts import ExpenseCategory, ItemStatus, Route
from pydantic import BaseModel, ConfigDict, Field


class Severity(IntEnum):
    """How strongly a policy finding constrains the route.

    Ordered so ``max(severities)`` yields the most constraining one.
    """

    INFO = 0  # informational; does not by itself change the route
    HARD_STOP = 1  # forces human_review regardless of amount/confidence
    HIGH = 2  # forces reject (not reimbursable)


class PolicyFinding(BaseModel):
    """A single deterministic observation, tagged with its policy rule id."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    severity: Severity
    message: str


class AutonomyThresholds(BaseModel):
    """Tunable policy numbers (policy.md §6 defaults).

    In production these are served by the config service so they change
    without a redeploy (F7/M13). The defaults here match policy.md.

    FX rates are validated strictly positive here too (defense in depth —
    the config service is the mutation gate): if a poisoned value ever
    reaches the decision service, validation fails and `fetch_thresholds`
    falls back to these safe defaults instead of converting totals to $0.
    """

    ceiling_usd: Decimal = Decimal("250")
    min_confidence: float = 0.80

    meal_per_attendee_usd: Decimal = Decimal("75")
    client_entertainment_over_usd: Decimal = Decimal("500")
    saas_monthly_cap_usd: Decimal = Decimal("200")
    hardware_cap_usd: Decimal = Decimal("1000")
    travel_single_over_usd: Decimal = Decimal("1500")
    receipt_required_over_usd: Decimal = Decimal("25")
    fx_hard_stop_usd: Decimal = Decimal("1000")

    fx_rates: dict[str, Annotated[Decimal, Field(gt=0)]] = Field(
        default_factory=lambda: {
            "USD": Decimal("1"),
            "EUR": Decimal("1.08"),
            "GBP": Decimal("1.27"),
        }
    )


class PolicyEvaluation(BaseModel):
    """Output of the deterministic policy engine for one invoice."""

    usd_amount: Decimal
    category: ExpenseCategory
    category_compliant: bool
    findings: list[PolicyFinding]

    @property
    def hard_stops(self) -> list[PolicyFinding]:
        return [f for f in self.findings if f.severity == Severity.HARD_STOP]

    @property
    def rejections(self) -> list[PolicyFinding]:
        return [f for f in self.findings if f.severity == Severity.HIGH]

    @property
    def rule_ids(self) -> list[str]:
        # Preserve order, drop duplicates.
        return list(dict.fromkeys(f.rule_id for f in self.findings))


class Recommendation(StrEnum):
    """The agent's advisory recommendation. It has no authority."""

    APPROVE = "approve"
    ESCALATE = "escalate"
    REJECT = "reject"


class AgentAssessment(BaseModel):
    """Structured output from the MAF agent (M12: recommendation is advisory).

    The router reads ``confidence`` (only ever to *downgrade* to human) and,
    for high-severity policy citations, ``cited_rules``. It never lets
    ``recommendation`` upgrade an item to auto-approve.
    """

    category: ExpenseCategory
    risk: str = "unknown"
    confidence: float = Field(ge=0.0, le=1.0)
    cited_rules: list[str] = Field(default_factory=list)
    policy_violations: list[str] = Field(default_factory=list)
    fraud_signals: list[str] = Field(default_factory=list)
    recommendation: Recommendation = Recommendation.ESCALATE
    reasoning: str = ""


class RouteDecision(BaseModel):
    """The final, deterministic decision for an item."""

    route: Route
    reason: str
    cited_rules: list[str] = Field(default_factory=list)
    confidence: float | None = None


# The submitter-visible status each route maps to when the decision is made.
ROUTE_TO_STATUS: dict[Route, ItemStatus] = {
    Route.AUTO_APPROVE: ItemStatus.AUTO_APPROVED,
    Route.HUMAN_REVIEW: ItemStatus.PENDING_APPROVAL,
    Route.REJECT: ItemStatus.REJECTED,
    Route.DUPLICATE: ItemStatus.DUPLICATE,
}


class DecisionRecord(BaseModel):
    """The full, auditable decision for one item (F9)."""

    tracking_id: str
    correlation_id: str
    invoice_id: str
    route: Route
    status: ItemStatus
    reason: str
    cited_rules: list[str] = Field(default_factory=list)
    usd_amount: str
    assessment: AgentAssessment
    decided_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))

