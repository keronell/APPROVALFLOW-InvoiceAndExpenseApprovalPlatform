"""Deterministic decisioning core for ApprovalFlow.

The agent (Microsoft Agent Framework) only *recommends* — it returns an
``AgentAssessment``. The pure :func:`route` function in :mod:`.router` makes the
final, provable decision. The autonomy ceiling is checked against USD amounts
computed by :mod:`.policy_engine`, never against anything the LLM produced, so
the ceiling cannot be exceeded even when the agent is forced to approve (M12/F10).
"""

from .models import (
    AgentAssessment,
    AutonomyThresholds,
    PolicyEvaluation,
    PolicyFinding,
    Recommendation,
    RouteDecision,
    Severity,
)
from .policy_engine import evaluate
from .router import route

__all__ = [
    "AgentAssessment",
    "AutonomyThresholds",
    "PolicyEvaluation",
    "PolicyFinding",
    "Recommendation",
    "RouteDecision",
    "Severity",
    "evaluate",
    "route",
]
