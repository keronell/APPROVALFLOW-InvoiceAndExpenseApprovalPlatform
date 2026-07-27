# Known bugs

Open defects we know about but have not fixed yet. Each entry names the failure,
the code path that causes it, what the user sees, and the intended fix.

Status legend: **open** — no fix in flight · **fix in prep** — fix is being
prepared on a branch, not yet merged to `main`.

---

## 1. A submission can become unfindable if the claim succeeds but the write fails

**Status:** open
**Where:** `services/intake/approvalflow_intake/service.py:66` (claim) and
`:80` / `:93` (persist); read path `service.py:104` → `app.py:71`

Intake accepts a submission in two steps that are **not atomic**:

```python
owner = await self._store.claim_fingerprint(fingerprint, tracking_id)  # step 1
...
await self._store.put(record)                                          # step 2
```

The same shape exists for `claim_idempotency_key` (`service.py:44`).

If step 1 succeeds and step 2 fails (state store blip, pod killed between the
two calls), the fingerprint/idempotency key is now permanently owned by a
`tracking_id` that has **no record behind it**. From then on:

- `GET /api/invoices/{tracking_id}` → `404 Unknown tracking id`, because
  `get_status` returns `None` — there is nothing to find.
- Resubmitting the same invoice does not help: the fingerprint is already
  claimed, so the new submission short-circuits to `duplicate` with
  `duplicateOf` pointing at the phantom tracking id (`service.py:68-82`).

Net effect: the invoice is invisible *and* unsubmittable, and the only way out
is an operator deleting the orphaned claim key from the state store.

**Intended fix:** make claim + persist recoverable rather than assuming both
land. Options: write the record first and claim second (a lost claim only costs
a missed duplicate check, which is recoverable), give claim keys a short TTL
that is confirmed on successful `put`, or treat "claim exists but record
missing" as reclaimable on the next submit instead of as a duplicate.

---

## 2. A sent-back invoice is terminal — the submitter cannot resubmit it

**Status:** open
**Where:** `services/decision/approvalflow_decision/service/workflow.py:180-183`,
`services/intake/approvalflow_intake/service.py:65-82`

When an approver picks **send back**, the workflow sets the item to
`ItemStatus.SENT_BACK` with the message *"please resubmit with the requested
information"* — but nothing in the system supports that resubmission:

- The decision workflow has completed. `SENT_BACK` is a final state; there is no
  resume path back into review.
- The submitter's only route back in is `POST /api/invoices`, and the corrected
  invoice still has the same fingerprint (vendor + invoice number + total), so
  intake claims it as a **duplicate** of the sent-back item and never publishes
  `item.submitted`. The pipeline never runs again.
- The UI reflects this: `sent_back` renders as a `warn` badge
  (`ui/src/components.jsx:7`) and reporting counts it as an outcome
  (`services/reporting/approvalflow_reporting/service.py:152`), but neither
  offers a next action.

So "send back" behaves in practice as a soft reject: the item is closed and the
requested information can never be supplied.

**Intended fix:** make send-back a real round trip — e.g. a
`POST /api/invoices/{trackingId}/resubmit` on intake that only accepts items in
`SENT_BACK`, re-publishes `item.submitted` under the same tracking id (so the
correlation id survives, M14/F9), and either bumps the fingerprint with a
revision suffix or is exempted from the duplicate check for that tracking id.

---

## 3. Payment succeeds but the completion step fails → the user is asked to pay again

**Status:** fix in prep (branch `prep`)
**Where:** `services/payment/approvalflow_payment/workflow.py:55-57` and
`:90-129`

The saga runs the external charge and the "mark it done" step as separate
activities:

```python
yield ctx.call_activity(pay_activity, input=req)      # money actually moves
yield ctx.call_activity(confirm_activity, input=req)  # budget confirm
yield ctx.call_activity(complete_activity, input=req) # publish PAID
```

`pay_activity` is genuinely idempotent — it records `payment::<trackingId>` as
`paid` and short-circuits on replay (`workflow.py:98-101`), and the provider is
called with a stable idempotency key. The problem is what happens **after** it:
if `confirm_activity` or `complete_activity` exhausts its retries, the
`payment.completed` / `item.status-changed` events are never published, so
intake never moves the item to `PAID` (`service.py:107`) and reporting never
counts it.

The vendor has been charged, but every surface the user can see still says the
invoice is unpaid — which pushes the submitter (or an operator) into paying it
again through a path that does not reuse the original idempotency key. The
recorded `paid` state exists, but nothing reads it to answer "was this already
charged?" outside of a workflow replay.

**Intended fix (on `prep`):** treat the durable `payment::<trackingId>` record
as the source of truth for "already charged", and make the post-charge steps
recoverable — replay the completion from the stored payment state rather than
re-running the charge, and surface `paid` state on the status endpoint so no
human is ever told to pay twice.
