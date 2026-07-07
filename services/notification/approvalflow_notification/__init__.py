"""Notification service — closes the M8 loop.

Intake acknowledges immediately (202 + tracking id); this service delivers the
FINAL result later on a notification channel: it watches the pipeline's events
and files a per-submitter inbox entry when an item reaches a terminal outcome
(paid, payment_failed, rejected, sent_back). The submitter reads their inbox
without knowing tracking ids.
"""
