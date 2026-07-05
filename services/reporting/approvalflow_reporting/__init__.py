"""Reporting service — the controller dashboard and the auditor's trail.

Subscribes to every pipeline event, aggregates throughput / auto-vs-human
metrics (F8), and appends each event to a per-correlation-id audit trail so an
auditor can pull the complete decision history of any item (F9).
"""
