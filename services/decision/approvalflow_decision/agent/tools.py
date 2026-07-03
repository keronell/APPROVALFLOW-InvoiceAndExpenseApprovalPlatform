"""Typed tools the agent may call (MAF @tool).

`fetch_policy` is the file-first RAG seam (N5): it returns only the policy
clause(s) relevant to an item's category instead of dumping the whole document
into the prompt. `lookup_vendor` answers the known-vendor question.
"""

from __future__ import annotations

from agent_framework import tool

# Minimal, self-contained policy snippets keyed by category. Stage N5 can swap
# this for a grep/chunk over Examples/policy.md without changing the agent.
_POLICY_SNIPPETS: dict[str, str] = {
    "meals": (
        "MEAL-01: personal/team meals up to $75/attendee; attendee count required. "
        "MEAL-02: client entertainment over $500 needs a client name + justification. "
        "MEAL-03: alcohol-only receipts are not reimbursable."
    ),
    "travel": (
        "TRAVEL-01: economy flights, standard hotels, economy ground transport are eligible. "
        "TRAVEL-02: any single travel expense over $1,500 needs manager approval. "
        "TRAVEL-03: first/business-class always needs approval."
    ),
    "saas": "SAAS-01: subscriptions are eligible up to $200/month.",
    "hardware": (
        "HW-01: hardware eligible up to $1,000. "
        "HW-02: hardware over $1,000 is a Capital expense → always human-approved."
    ),
    "other": "No category-specific cap; rely on the global rules and the autonomy ceiling.",
}

_GLOBAL_RULES = (
    "GLOBAL-RECEIPT: receipt required over $25. GLOBAL-VENDOR: new vendors are always reviewed. "
    "GLOBAL-FX: foreign currency converts to USD; over $1,000 is a hard stop. "
    "GLOBAL-MATH: line items + tax must reconcile to total. "
    "GLOBAL-FRAUD: round-number + new-vendor + no-detail/off-hours signals are a hard stop."
)


@tool
def fetch_policy(category: str) -> str:
    """Return the policy clauses relevant to an expense category.

    Args:
        category: one of meals, travel, saas, hardware, other.
    """
    snippet = _POLICY_SNIPPETS.get(category.lower().strip(), _POLICY_SNIPPETS["other"])
    return f"{snippet}\n{_GLOBAL_RULES}"


# Known vendors — in production this is a service/db lookup; here it is seeded
# from the vendors that appear as `vendorKnown: true` in the fixtures.
_KNOWN_VENDORS = {
    "bistro 19",
    "atlassian",
    "the rooftop grill",
    "dell",
    "trattoria verde",
    "office depot",
    "hotel adler",
    "lakeside venue",
    "rackspace supplies",
    "pixelforge",
    "expoworks",
    "city cabs",
    "logitech",
    "datadog",
    "lufthansa",
}


@tool
def lookup_vendor(name: str) -> str:
    """Return whether a vendor is already known/approved.

    Args:
        name: the vendor name to look up.
    """
    known = name.lower().strip() in _KNOWN_VENDORS
    return "known" if known else "unknown"
