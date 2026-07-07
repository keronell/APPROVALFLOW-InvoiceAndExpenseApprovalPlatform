import { useCallback, useEffect, useState } from "react";
import { SAMPLES } from "./samples.js";

const STATUS_COLORS = {
  paid: "ok", auto_approved: "ok", approved: "ok",
  pending_approval: "warn", received: "warn", processing: "warn", paying: "warn",
  rejected: "bad", payment_failed: "bad", duplicate: "bad", sent_back: "warn",
};

function Badge({ status }) {
  return <span className={`badge ${STATUS_COLORS[status] || ""}`}>{status || "…"}</span>;
}

async function jsonFetch(url, options) {
  const resp = await fetch(url, options);
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return resp.json();
}

// ---------------------------------------------------------------- Submit view
function SubmitView({ onSubmitted }) {
  const [body, setBody] = useState(JSON.stringify(Object.values(SAMPLES)[0], null, 2));
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  const submit = async () => {
    setError("");
    try {
      const accepted = await jsonFetch("/api/invoices", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      setResult(accepted);
      onSubmitted(accepted.trackingId);
    } catch (e) {
      setError(String(e.message));
    }
  };

  return (
    <section>
      <h2>Submit an invoice / expense</h2>
      <div className="samples">
        {Object.entries(SAMPLES).map(([label, sample]) => (
          <button key={label} className="chip"
            onClick={() => setBody(JSON.stringify(sample, null, 2))}>
            {label}
          </button>
        ))}
      </div>
      <textarea rows={16} value={body} onChange={(e) => setBody(e.target.value)} />
      <button className="primary" onClick={submit}>Submit</button>
      {result && (
        <p className="note">
          Accepted — tracking id <code>{result.trackingId}</code> (status: {result.status})
        </p>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  );
}

// ----------------------------------------------------------------- Track view
function TrackView({ trackingId, setTrackingId }) {
  const [record, setRecord] = useState(null);
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
      {error && <p className="error">{error}</p>}
    </section>
  );
}

// ------------------------------------------------------------- Approvals view
function ApprovalsView() {
  const [queue, setQueue] = useState([]);

  const refresh = useCallback(async () => {
    try {
      setQueue(await jsonFetch("/api/approvals"));
    } catch { /* gateway may still be warming up */ }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => clearInterval(timer);
  }, [refresh]);

  const act = async (trackingId, action) => {
    await jsonFetch(`/api/approvals/${trackingId}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, approver: "approver@northwind.example" }),
    });
    refresh();
  };

  return (
    <section>
      <h2>Approval queue <small>({queue.length} pending)</small></h2>
      {queue.length === 0 && <p className="note">Nothing waiting for a human. 🎉</p>}
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

// ----------------------------------------------------------------- Inbox view
function InboxView() {
  const [submitter, setSubmitter] = useState("dana.cohen@northwind.example");
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

// ------------------------------------------------------------- Dashboard view
function DashboardView() {
  const [summary, setSummary] = useState(null);

  const refresh = useCallback(async () => {
    try {
      setSummary(await jsonFetch("/api/reports/summary"));
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => clearInterval(timer);
  }, [refresh]);

  if (!summary) return <section><h2>Dashboard</h2><p className="note">Loading…</p></section>;

  const tiles = [
    ["Submitted", summary.submitted],
    ["Auto-approved", summary.auto_approved],
    ["Escalated", summary.escalated],
    ["Auto-approval rate", `${Math.round(summary.auto_approval_rate * 100)}%`],
    ["Human approved", summary.human_approved],
    ["Human rejected", summary.human_rejected],
    ["Paid", summary.paid],
    ["Payment failed", summary.payment_failed],
    ["$ auto-approved", `$${summary.auto_approved_usd}`],
    ["$ human-approved", `$${summary.human_approved_usd}`],
  ];
  return (
    <section>
      <h2>Dashboard</h2>
      <div className="tiles">
        {tiles.map(([label, value]) => (
          <div className="tile" key={label}>
            <div className="value">{value}</div>
            <div className="label">{label}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

// ------------------------------------------------------------------------ App
const TABS = ["Submit", "Track", "Inbox", "Approvals", "Dashboard"];

export default function App() {
  const [tab, setTab] = useState("Submit");
  const [trackingId, setTrackingId] = useState("");

  return (
    <main>
      <header>
        <h1>ApprovalFlow</h1>
        <p className="tagline">The agent recommends — the deterministic router decides.</p>
        <nav>
          {TABS.map((t) => (
            <button key={t} className={t === tab ? "tab active" : "tab"} onClick={() => setTab(t)}>
              {t}
            </button>
          ))}
        </nav>
      </header>
      {tab === "Submit" && (
        <SubmitView onSubmitted={(tid) => { setTrackingId(tid); setTab("Track"); }} />
      )}
      {tab === "Track" && <TrackView trackingId={trackingId} setTrackingId={setTrackingId} />}
      {tab === "Inbox" && <InboxView />}
      {tab === "Approvals" && <ApprovalsView />}
      {tab === "Dashboard" && <DashboardView />}
    </main>
  );
}
