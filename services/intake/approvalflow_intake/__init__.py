"""Intake service — the front door of ApprovalFlow.

Accepts a submission, returns an immediate ``202 + tracking id`` (F1/M8),
guarantees exactly-one-effect for duplicate business submissions and retried
requests (F3/M10), then publishes ``item.submitted`` for the pipeline.
"""
