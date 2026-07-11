import { useCallback, useEffect, useState } from "react";
import { jsonFetch } from "../api.js";

export default function DashboardView() {
  const [summary, setSummary] = useState(null);
  const [forbidden, setForbidden] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setSummary(await jsonFetch("/api/reports/summary"));
      setForbidden(false);
    } catch (e) {
      if (e.status === 401 || e.status === 403) setForbidden(true);
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => clearInterval(timer);
  }, [refresh]);

  if (forbidden) {
    return (
      <section>
        <h2>Dashboard</h2>
        <p className="note">Sign in with the approver or admin role to see the metrics.</p>
      </section>
    );
  }
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
