"""Default policy + thresholds (policy.md §6). The single seed for the config
store; a controller may override any value at runtime."""

# Field names match approvalflow_decision.models.AutonomyThresholds so the
# decision service can validate this straight into that model.
DEFAULT_THRESHOLDS: dict = {
    "ceiling_usd": "250",
    "min_confidence": 0.80,
    "meal_per_attendee_usd": "75",
    "client_entertainment_over_usd": "500",
    "saas_monthly_cap_usd": "200",
    "hardware_cap_usd": "1000",
    "travel_single_over_usd": "1500",
    "receipt_required_over_usd": "25",
    "fx_hard_stop_usd": "1000",
    "fx_rates": {"USD": "1", "EUR": "1.08", "GBP": "1.27"},
}

DEFAULT_POLICY_SUMMARY = (
    "Northwind Components expense policy. Auto-approve only in-policy items at or "
    "below the autonomy ceiling from known vendors with a receipt; escalate new "
    "vendors, capital expenditure, FX/fraud/math hard stops, and anything over the "
    "ceiling or below the confidence threshold; reject non-reimbursable items."
)
