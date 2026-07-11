import { useState } from "react";
import { AuthBar } from "./components.jsx";
import SubmitView from "./views/SubmitView.jsx";
import TrackView from "./views/TrackView.jsx";
import InboxView from "./views/InboxView.jsx";
import ApprovalsView from "./views/ApprovalsView.jsx";
import DashboardView from "./views/DashboardView.jsx";

const TABS = ["Submit", "Track", "Inbox", "Approvals", "Dashboard"];

export default function App() {
  const [tab, setTab] = useState("Submit");
  const [trackingId, setTrackingId] = useState("");
  const [user, setUser] = useState("dana.cohen@northwind.example");
  const [role, setRole] = useState("admin");
  // Identity the installed JWT was issued for; null while sign-in is in flight.
  const [auth, setAuth] = useState(null);

  return (
    <main>
      <header>
        <div className="masthead">
          <div>
            <h1>ApprovalFlow</h1>
            <p className="tagline">The agent recommends — the deterministic router decides.</p>
          </div>
          <AuthBar user={user} setUser={setUser} role={role} setRole={setRole} onAuth={setAuth} />
        </div>
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
      {tab === "Inbox" && <InboxView user={user} />}
      {tab === "Approvals" && <ApprovalsView auth={auth} />}
      {tab === "Dashboard" && <DashboardView />}
    </main>
  );
}
