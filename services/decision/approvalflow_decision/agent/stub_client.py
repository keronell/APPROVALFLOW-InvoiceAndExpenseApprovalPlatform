"""Deterministic, offline MAF chat client (LLM_PROVIDER=stub).

Used by CI and the verification command so tests never hit a real model or a
rate limit. It is a genuine ``BaseChatClient``, so the real Microsoft Agent
Framework ``Agent`` loop runs end-to-end in CI — only the model is replaced.

It parses the invoice embedded in the prompt and returns a plausible
``AgentAssessment``. Crucially it decides from *structured* fields only and
ignores the free-text ``notes`` — modelling an agent that cannot be steered by
an "approve me" memo (the router is the real guarantee; this keeps the stub
honest to the same contract).
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

from agent_framework import BaseChatClient, ChatResponse, Content, Message

from ..models import AgentAssessment, Recommendation

_INVOICE_RE = re.compile(r"<invoice>(.*?)</invoice>", re.DOTALL)

_PRIMARY_RULE = {
    "meals": ["MEAL-01"],
    "travel": ["TRAVEL-01"],
    "saas": ["SAAS-01"],
    "hardware": ["HW-01"],
    "other": [],
}


def _message_text(messages: Any) -> str:
    parts: list[str] = []
    for message in messages:
        for content in getattr(message, "contents", []) or []:
            text = getattr(content, "text", None)
            if text:
                parts.append(text)
    return "\n".join(parts)


def assess_invoice(invoice: dict) -> AgentAssessment:
    """Pure, deterministic assessment from an invoice dict."""
    category = str(invoice.get("category", "other")).lower()
    vendor_known = bool(invoice.get("vendorKnown", False))
    total = Decimal(str(invoice.get("total", "0")))
    line_items = invoice.get("lineItems", []) or []
    # Content signals (line-item descriptions are data, not instructions).
    descriptions = " ".join(str(li.get("description", "")) for li in line_items).lower()

    alcohol = "alcohol" in descriptions
    round_number = total >= Decimal("1000") and total % Decimal("1000") == 0
    fraud = (not vendor_known) and round_number and len(line_items) <= 1

    confidence = 0.60 if category == "other" else 0.90

    if alcohol:
        recommendation = Recommendation.REJECT
        violations = ["MEAL-03"]
        risk = "high"
    elif not vendor_known or fraud:
        recommendation = Recommendation.ESCALATE
        violations = ["GLOBAL-VENDOR"] if not vendor_known else ["GLOBAL-FRAUD"]
        risk = "high" if fraud else "medium"
    else:
        recommendation = Recommendation.APPROVE
        violations = []
        risk = "low"

    return AgentAssessment(
        category=category if category in _PRIMARY_RULE else "other",
        risk=risk,
        confidence=confidence,
        cited_rules=_PRIMARY_RULE.get(category, []),
        policy_violations=violations,
        fraud_signals=["round-number amount to a new vendor"] if fraud else [],
        recommendation=recommendation,
        reasoning=f"[stub] category={category}, vendor_known={vendor_known}, total={total}.",
    )


class StubChatClient(BaseChatClient):
    """Returns a deterministic AgentAssessment for the invoice in the prompt."""

    async def _inner_get_response(self, *, messages, stream, options, **kwargs) -> ChatResponse:
        text = _message_text(messages)
        match = _INVOICE_RE.search(text)
        if match:
            invoice = json.loads(match.group(1))
            assessment = assess_invoice(invoice)
        else:
            # No invoice found — escalate with low confidence rather than guess.
            assessment = AgentAssessment(
                category="other",
                confidence=0.0,
                recommendation=Recommendation.ESCALATE,
                reasoning="[stub] no invoice payload found in prompt.",
            )
        body = assessment.model_dump_json()
        message = Message(role="assistant", contents=[Content.from_text(body)])
        return ChatResponse(messages=[message])
