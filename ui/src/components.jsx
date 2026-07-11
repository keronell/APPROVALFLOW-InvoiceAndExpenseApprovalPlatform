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
// onAuth reports the identity the installed token was actually issued for
// (null while a fetch is in flight or failed), so views never act under a
// token that doesn't match the typed user/role yet.
export function AuthBar({ user, setUser, role, setRole, onAuth }) {
  const [signedIn, setSignedIn] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setSignedIn(false);
    onAuth(null);
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
          onAuth({ user: data.user, role: data.role });
        }
      } catch {
        if (!cancelled) setSignedIn(false);
      }
    })();
    return () => { cancelled = true; };
  }, [user, role, onAuth]);

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
