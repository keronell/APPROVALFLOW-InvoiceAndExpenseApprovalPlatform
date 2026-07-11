import { useCallback, useEffect, useState } from "react";
import { jsonFetch } from "../api.js";

export default function ApprovalsView({ user }) {
  const [queue, setQueue] = useState([]);
  const [notes, setNotes] = useState({});
  const [forbidden, setForbidden] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      setQueue(await jsonFetch("/api/approvals"));
      setForbidden(false);
    } catch (e) {
      // Gateway may still be warming up; only surface a real role problem.
      if (e.status === 401 || e.status === 403) setForbidden(true);
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => clearInterval(timer);
  }, [refresh]);

  const act = async (trackingId, action) => {
    setError("");
    try {
      await jsonFetch(`/api/approvals/${trackingId}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, approver: user, note: notes[trackingId] || "" }),
      });
      setNotes((n) => ({ ...n, [trackingId]: "" }));
      refresh();
    } catch (e) {
      // Surface the failure instead of letting the click look like a no-op.
      setError(`Could not ${action.replace("_", " ")}: ${e.message}`);
    }
  };

  return (
    <section>
      <div className="section-head">
        <h2>Approval queue <small>({queue.length} pending)</small></h2>
        <span className="note">deciding as <code>{user}</code></span>
      </div>
      {error && <p className="error">{error}</p>}
      {forbidden && (
        <p className="note">Sign in with the approver or admin role to work the queue.</p>
      )}
      {!forbidden && queue.length === 0 && <p className="note">Nothing waiting for a human. 🎉</p>}
      {queue.map((item) => {
        const esc = item.escalation;
        return (
          <div className="card" key={esc.trackingId}>
            <p>
              <strong>{esc.invoiceId}</strong> — {esc.vendor} — ${esc.usdAmount} —{" "}
              {esc.department}
            </p>
            <p className="reason">{esc.reason}</p>
            <p>
              Agent: <em>{esc.recommendation}</em> (confidence{" "}
              {Math.round(esc.confidence * 100)}%) · rules:{" "}
              {esc.citedRules.map((r) => <code key={r}>{r}</code>)}
            </p>
            <input placeholder="optional note for the audit trail"
              value={notes[esc.trackingId] || ""}
              onChange={(e) => setNotes((n) => ({ ...n, [esc.trackingId]: e.target.value }))} />
            <div className="row">
              <button className="primary" onClick={() => act(esc.trackingId, "approve")}>Approve</button>
              <button className="danger" onClick={() => act(esc.trackingId, "reject")}>Reject</button>
              <button onClick={() => act(esc.trackingId, "send_back")}>Send back</button>
            </div>
          </div>
        );
      })}
    </section>
  );
}
