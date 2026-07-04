"""Payment service — the saga that actually moves money.

ReserveBudget -> ExecutePayment -> ConfirmPayment, with a ReleaseBudget
compensation on failure. Department budgets use ETag optimistic concurrency so
two concurrent approvals can never overspend (M9); every step is idempotent so
a redelivered event or retried payment has exactly one effect (M10).
"""
