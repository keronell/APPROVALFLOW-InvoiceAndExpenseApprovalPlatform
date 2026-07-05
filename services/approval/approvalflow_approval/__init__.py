"""Approval service — the human-in-the-loop side of ApprovalFlow.

Holds the escalation queue (only items the system escalated, each with the
agent's recommendation, confidence, and cited rules — F4). An approver acts
once (approve / reject / send-back — F5) and the decision workflow resumes
exactly where it paused, surviving restarts (M11).
"""
