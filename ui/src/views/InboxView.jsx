import { useCallback, useEffect, useState } from "react";
import { jsonFetch } from "../api.js";
import { Badge } from "../components.jsx";

export default function InboxView({ user }) {
  const [submitter, setSubmitter] = useState(user);
  const [inbox, setInbox] = useState([]);

  const refresh = useCallback(async () => {
    if (!submitter) return;
    try {
      setInbox(await jsonFetch(`/api/notifications?submitter=${encodeURIComponent(submitter)}`));
    } catch { /* ignore while warming up */ }
  }, [submitter]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => clearInterval(timer);
  }, [refresh]);

  return (
    <section>
      <h2>Inbox <small>(final outcomes for a submitter)</small></h2>
      <div className="row">
        <input placeholder="submitter email" value={submitter}
          onChange={(e) => setSubmitter(e.target.value.trim())} />
        <button onClick={refresh}>Refresh</button>
      </div>
      {inbox.length === 0 && <p className="note">No notifications yet.</p>}
      {inbox.map((n) => (
        <div className="card" key={`${n.tracking_id}-${n.status}`}>
          <p><strong>{n.invoice_id}</strong> — <Badge status={n.status} /></p>
          <p className="reason">{n.reason}</p>
          <p className="note">tracking id: <code>{n.tracking_id}</code></p>
        </div>
      ))}
    </section>
  );
}
