import { useState, type KeyboardEvent } from "react";
import { ApiError, login, register } from "../../api/client";
import { INJECTED_KEY } from "../../config";
import { adoptUser, getState, savePrefs, setState, useStore } from "../../store";
import { start } from "../session";

/**
 * The backend, model and base URL are the deployment's to decide (serve.py
 * injects them), so all the user gives is who they are — plus the model key when
 * the deployment didn't serve one. Progress shows on the button; only a failure
 * gets a line of text, and a short one.
 */
function shortError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 0) return "Cannot reach the server. Try again in a moment.";
    if (e.status === 400 || e.status === 422 || e.status === 403 || e.status === 429) return e.message;
    return `Sign-in failed (${e.status || "network"}). Try again, or contact the administrator.`;
  }
  return "Sign-in failed. Try again, or contact the administrator.";
}

export function AuthScreen() {
  const notice = useStore((s) => s.authNotice);
  const [email, setEmail] = useState(getState().email);
  const [password, setPassword] = useState("");
  const [apiKey, setApiKey] = useState(INJECTED_KEY ? "" : getState().apiKey);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<"" | "login" | "register">("");

  async function doAuth(mode: "login" | "register") {
    const key = INJECTED_KEY || apiKey.trim();
    const who = email.trim();
    if (!who || !password) { setError("Enter your email and password."); return; }
    if (!key) { setError("Enter the model API key."); return; }

    // Whoever signed in last may not be who is signing in now: take only this
    // user's shares and session, never the previous account's.
    if (who !== getState().email) adoptUser(who);
    setState({ email: who, password, apiKey: key, authNotice: "" });
    savePrefs();

    setPending(mode);
    setError("");
    try {
      if (mode === "register") await register(who, password);   // "exists" just signs in
      await login(who, password);
      await start();
    } catch (e) {
      setError(shortError(e));
    } finally {
      setPending("");
    }
  }

  const onEnter = (e: KeyboardEvent) => { if (e.key === "Enter") doAuth("login"); };

  return (
    <div id="auth">
      <h1>OSINT</h1>
      <div className="sub">A private knowledge base you can ask questions.</div>
      {notice && <div className="note warn">{notice}</div>}

      <label htmlFor="email">Email</label>
      <input id="email" type="email" autoComplete="username" placeholder="you@example.com"
        value={email} onChange={(e) => setEmail(e.target.value)} onKeyDown={onEnter} autoFocus={!email} />

      <label htmlFor="password">Password</label>
      <input id="password" type="password" autoComplete="current-password" placeholder="min. 8 characters"
        value={password} onChange={(e) => setPassword(e.target.value)} onKeyDown={onEnter} autoFocus={!!email} />

      {!INJECTED_KEY && (
        <>
          <label htmlFor="apiKey">Model API key</label>
          <input id="apiKey" type="password" placeholder="sk-..."
            value={apiKey} onChange={(e) => setApiKey(e.target.value)} onKeyDown={onEnter} />
        </>
      )}

      <div className="row">
        <button className="primary" disabled={!!pending} onClick={() => doAuth("login")}>
          {pending === "login" ? "Signing in…" : "Sign in"}
        </button>
        <button disabled={!!pending} onClick={() => doAuth("register")}>
          {pending === "register" ? "Registering…" : "Register"}
        </button>
      </div>
      {error && <div className="note err">{error}</div>}
    </div>
  );
}
