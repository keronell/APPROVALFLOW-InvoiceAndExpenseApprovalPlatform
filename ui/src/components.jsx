import { useEffect, useState } from "react";
import { setAuthToken } from "./api.js";

const STATUS_COLORS = {
  paid: "ok", auto_approved: "ok", approved: "ok",
  pending_approval: "warn", received: "warn", processing: "warn", paying: "warn",
  rejected: "bad", payment_failed: "bad", duplicate: "bad", sent_back: "warn",
};

export function Badge({ status }) {
  return <span className={`badge ${STATUS_COLORS[status] || ""}`}>{status || "…"}</span>;
}

const ROLES = ["submitter", "approver", "admin"];

// Dev sign-in (N1): trade user+role for a self-signed JWT at the gateway.
export function AuthBar({ user, setUser, role, setRole }) {
  const [signedIn, setSignedIn] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch("/api/auth/token", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ user, role }),
        });
        if (!resp.ok) throw new Error(`${resp.status}`);
        const data = await resp.json();
        if (!cancelled) {
          setAuthToken(data.token);
          setSignedIn(true);
        }
      } catch {
        if (!cancelled) setSignedIn(false);
      }
    })();
    return () => { cancelled = true; };
  }, [user, role]);

  return (
    <div className="row authbar">
      <input value={user} onChange={(e) => setUser(e.target.value.trim())}
        placeholder="user email" title="Signed-in user" />
      <select value={role} onChange={(e) => setRole(e.target.value)} title="Role (N1)">
        {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
      </select>
      <span className={signedIn ? "note signed-in" : "note"}>
        {signedIn ? `signed in as ${role}` : "signing in…"}
      </span>
    </div>
  );
}
