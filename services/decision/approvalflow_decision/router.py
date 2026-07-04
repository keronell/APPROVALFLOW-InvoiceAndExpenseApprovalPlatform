"""The deterministic router: classifier proposes, router decides (M12/F10).

``route`` is a pure function. Auto-approval is only ever reached in the final
branch, where **every** condition has already passed:

    USD amount ≤ ceiling  AND  confidence ≥ threshold  AND  category-compliant
    AND  no hard stop      AND  no rejection

The ceiling comparison uses ``evaluation.usd_amount`` (computed deterministically
by the policy engine) — never a number produced by the LLM. The agent's
``recommendation`` is never read here, so forcing the agent to say "approve"
cannot upgrade the route. Confidence can only *downgrade* to human review.
"""

from __future__ import annotations

from approvalflow_shared.contracts import Route

from .models import AgentAssessment, AutonomyThresholds, PolicyEvaluation, RouteDecision


def route(
    evaluation: PolicyEvaluation,
    assessment: AgentAssessment,
    thresholds: AutonomyThresholds | None = None,
    *,
    already_processed: bool = False,
) -> RouteDecision:
    """Decide the final route for one item.

    ``already_processed`` is set by intake when the item's fingerprint
    (vendor + invoiceNumber + total) matches an item already handled (GLOBAL-DUP).
    """
    thresholds = thresholds or AutonomyThresholds()

    # GLOBAL-DUP: a re-submission is a duplicate — no second decision, no payment.
    if already_processed:
        return RouteDecision(
            route=Route.DUPLICATE,
            reason=(
                "Duplicate of an already-processed invoice "
                "(same vendor, invoice number, and total)."
            ),
            cited_rules=["GLOBAL-DUP"],
            confidence=assessment.confidence,
        )

    # High-severity policy violation (e.g. alcohol-only) → reject, any amount.
    if evaluation.rejections:
        rejection = evaluation.rejections[0]
        return RouteDecision(
            route=Route.REJECT,
            reason=rejection.message,
            cited_rules=[f.rule_id for f in evaluation.rejections],
            confidence=assessment.confidence,
        )

    over_ceiling = evaluation.usd_amount > thresholds.ceiling_usd
    low_confidence = assessment.confidence < thresholds.min_confidence

    # Any of these forces human review. Collected together so the reason and
    # cited rules explain *every* factor that blocked auto-approval (F2/F4).
    blockers: list[str] = []
    cited: list[str] = list(evaluation.rule_ids)

    if evaluation.hard_stops:
        blockers.extend(f.message for f in evaluation.hard_stops)
    if not evaluation.category_compliant and not evaluation.hard_stops:
        blockers.append(f"Not policy-compliant for its category ({evaluation.category}).")
    if over_ceiling:
        blockers.append(
            f"Amount ${evaluation.usd_amount} is over the "
            f"${thresholds.ceiling_usd} autonomy ceiling."
        )
        cited.append("AUTONOMY-CEILING")
    if low_confidence:
        blockers.append(
            f"Agent confidence {assessment.confidence:.2f} is below the "
            f"{thresholds.min_confidence:.2f} threshold."
        )
        cited.append("AUTONOMY-CONFIDENCE")

    if blockers:
        return RouteDecision(
            route=Route.HUMAN_REVIEW,
            reason=" ".join(blockers),
            cited_rules=list(dict.fromkeys(cited)),
            confidence=assessment.confidence,
        )

    # Every condition passed — safe to auto-approve.
    return RouteDecision(
        route=Route.AUTO_APPROVE,
        reason=(
            f"Auto-approved: ${evaluation.usd_amount} is within the "
            f"${thresholds.ceiling_usd} ceiling, policy-compliant for its category, "
            f"no hard stops, agent confidence {assessment.confidence:.2f}."
        ),
        cited_rules=list(evaluation.rule_ids),
        confidence=assessment.confidence,
    )
