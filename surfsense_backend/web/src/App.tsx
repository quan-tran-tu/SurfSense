import { useEffect } from "react";
import { AdminView } from "./features/admin/AdminView";
import { AuthScreen } from "./features/auth/AuthScreen";
import { ChatView } from "./features/chat/ChatView";
import { SourcePanel } from "./features/citations/SourcePanel";
import { detectAdmin } from "./features/session";
import { Sidebar } from "./features/sidebar/Sidebar";
import { useStore } from "./store";
import { Toasts } from "./ui/toast";

// How often an open page re-reads the user's role, so a revoked admin loses the
// admin views even without touching anything.
const ROLE_CHECK_MS = 30_000;

export function App() {
  const signedIn = useStore((s) => s.signedIn);
  const boot = useStore((s) => s.boot);
  const isAdmin = useStore((s) => s.isAdmin);
  const adminOpen = useStore((s) => s.adminOpen && s.isAdmin);

  useEffect(() => {
    if (!signedIn || !isAdmin) return;
    const check = () => { if (document.visibilityState === "visible") void detectAdmin(); };
    const timer = setInterval(check, ROLE_CHECK_MS);
    document.addEventListener("visibilitychange", check);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", check); };
  }, [signedIn, isAdmin]);

  if (boot === "resuming") return <div id="auth" className="sub">Loading…</div>;
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
