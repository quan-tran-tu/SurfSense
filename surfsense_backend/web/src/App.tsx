import { useEffect, useState } from "react";
import { AdminView } from "./features/admin/AdminView";
import { AuthScreen } from "./features/auth/AuthScreen";
import { ChatView } from "./features/chat/ChatView";
import { SourcePanel } from "./features/citations/SourcePanel";
import { ReportCanvas } from "./features/report/ReportCanvas";
import { reportIdFromHash } from "./features/report/route";
import { detectAdmin } from "./features/session";
import { Sidebar } from "./features/sidebar/Sidebar";
import { useStore } from "./store";
import { Toasts } from "./ui/toast";

// How often an open page re-reads the user's role, so a revoked admin loses the
// admin views even without touching anything.
const ROLE_CHECK_MS = 30_000;

/**
 * The report shown by a canvas tab, following the hash (the version picker
 * moves it). Leaving the report route for the chat reloads the page, since the
 * chat app was never set up in a canvas tab.
 */
function useReportRoute() {
  const [id, setId] = useState(reportIdFromHash);
  useEffect(() => {
    const onHash = () => {
      const next = reportIdFromHash();
      if (id != null && next == null) location.reload();
      else setId(next);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [id]);
  return id;
}

export function App() {
  const signedIn = useStore((s) => s.signedIn);
  const boot = useStore((s) => s.boot);
  const isAdmin = useStore((s) => s.isAdmin);
  const adminOpen = useStore((s) => s.adminOpen && s.isAdmin);
  const reportId = useReportRoute();

  useEffect(() => {
    if (!signedIn || !isAdmin) return;
    const check = () => { if (document.visibilityState === "visible") void detectAdmin(); };
    const timer = setInterval(check, ROLE_CHECK_MS);
    document.addEventListener("visibilitychange", check);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", check); };
  }, [signedIn, isAdmin]);

  if (boot === "resuming") return <div id="auth" className="sub">Loading…</div>;
  if (reportId != null && signedIn) {
    return <><ReportCanvas key={reportId} id={reportId} /><Toasts /></>;
  }
  return (
    <>
      {!signedIn ? <AuthScreen /> : (
        <div id="app">
          <Sidebar />
          <ChatView />
          <SourcePanel />
        </div>
      )}
      {signedIn && adminOpen && <AdminView />}
      <Toasts />
    </>
  );
}
