import { useCallback, useEffect, useState } from "react";
import { jsonFetch } from "../api.js";
import { Badge } from "../components.jsx";

const TOPIC_LABELS = {
  "item.submitted": "Submitted",
  "decision.made": "Decision made",
  "escalation.requested": "Sent for human review",
  "escalation.resolved": "Human decided",
  "payment.requested": "Payment started",
  "payment.completed": "Paid",
  "payment.failed": "Payment failed",
  "item.status-changed": "Status changed",
};

const TOPIC_TONES = {
  "payment.completed": "ok",
  "payment.failed": "bad",
  "escalation.requested": "warn",
};

function entryDetail(entry) {
  const p = entry.payload || {};
  const bits = [];
  if (entry.topic === "decision.made" && p.route) bits.push(`route: ${p.route.replace("_", " ")}`);
  if (entry.topic === "escalation.resolved") {
    if (p.action) bits.push(p.action.replace("_", " "));
    if (p.approver) bits.push(`by ${p.approver}`);
  }
  if (entry.topic === "item.status-changed" && p.status) bits.push(p.status.replace("_", " "));
  if (p.reason) bits.push(p.reason);
  return bits.join(" — ");
}

function Timeline({ entries }) {
  const sorted = [...entries].sort((a, b) => new Date(a.at) - new Date(b.at));
  return (
    <div className="timeline">
      {sorted.map((entry) => (
        <div className="tl-entry" key={entry.topic}>
          <span className={`tl-dot ${TOPIC_TONES[entry.topic] || ""}`} />
          <div className="tl-body">
            <p className="tl-title">
              {TOPIC_LABELS[entry.topic] || entry.topic}
              <span className="tl-time">{new Date(entry.at).toLocaleTimeString()}</span>
            </p>
            {entryDetail(entry) && <p className="reason">{entryDetail(entry)}</p>}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function TrackView({ trackingId, setTrackingId }) {
  const [record, setRecord] = useState(null);
  const [trail, setTrail] = useState([]);
  const [trailForbidden, setTrailForbidden] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (!trackingId) return;
    setError("");
    try {
      setRecord(await jsonFetch(`/api/invoices/${trackingId}`));
    } catch (e) {
      setRecord(null);
      setError(String(e.message));
    }
    // Full trail is admin-gated (F9); the status card above works for any role.
    try {
      setTrail(await jsonFetch(`/api/audit/${trackingId}`));
      setTrailForbidden(false);
    } catch (e) {
      setTrail([]);
      setTrailForbidden(e.status === 401 || e.status === 403);
    }
  }, [trackingId]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 2500);
    return () => clearInterval(timer);
  }, [refresh]);

  return (
    <section>
      <h2>Track a submission</h2>
      <div className="row">
        <input placeholder="tracking id" value={trackingId}
          onChange={(e) => setTrackingId(e.target.value.trim())} />
        <button onClick={refresh}>Refresh</button>
      </div>
      {record && (
        <div className="card">
          <p><strong>{record.invoice?.id}</strong> — {record.invoice?.vendor} — ${record.invoice?.total}</p>
          <p>Status: <Badge status={record.status} /></p>
          <p className="reason">{record.reason}</p>
        </div>
      )}
      {record && trail.length > 0 && <Timeline entries={trail} />}
      {record && trailForbidden && (
        <p className="note">Sign in with the admin role to see the full audit timeline.</p>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  );
}
