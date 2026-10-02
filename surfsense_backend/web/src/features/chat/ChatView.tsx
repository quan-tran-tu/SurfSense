import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Markdown } from "../../lib/Markdown";
import type { Template } from "../../api/types";
import { useStore, type ChatMessage } from "../../store";
import { toggleSidebar, useLayout } from "../../ui/layout";
import { confirmAction } from "../../ui/confirm";
import { guard, toast } from "../../ui/toast";
import { clearScope } from "../scope";
import { SESSION_CONTEXT_MAX, setSessionContext } from "../sessionContext";
import { ask } from "./ask";
import { writeReport } from "./reports";

/**
 * The line under a message. While a reply is in flight it is a live indicator —
 * the phase ("Thinking", "Searching your documents", …) with moving dots;
 * afterwards it is plain text, an error or warning if anything.
 */
function Status({ m }: { m: ChatMessage }) {
  const live = m.streaming && !m.statusKind;
  if (!live) return <div className={`status ${m.statusKind ?? ""}`}>{m.status}</div>;
  // Nothing reported yet: the request is out and the server is still on it.
  const label = (m.status || (m.text ? "" : "Thinking")).replace(/\s*(…|\.{3})\s*$/, "");
  if (!label) return null;
  return (
    <div className="status live" role="status">
      <span className="shimmer">{label}</span>
      <span className="dots" aria-hidden="true"><i /><i /><i /></span>
    </div>
  );
}

function Message({ m }: { m: ChatMessage }) {
  if (m.role === "system") {
    return (
      <div className="msg system">
        <div className="bubble sys">{m.text}</div>
      </div>
    );
  }
  return (
    <div className={`msg ${m.role}`}>
      <div className="who">{m.role === "user" ? "You" : "Assistant"}</div>
      {/* No empty bubble with a lone cursor while waiting: the status says it. */}
      {(m.role === "user" || m.text) && (
        <div className={`bubble${m.streaming ? " cursor" : ""}`}>
          {m.role === "user" ? m.text : <Markdown text={m.text} msgId={m.id} />}
        </div>
      )}
      <Status m={m} />
    </div>
  );
}

function Messages() {
  const messages = useStore((s) => s.messages);
  const box = useRef<HTMLDivElement>(null);
  // Follow the conversation as it grows, streaming included.
  useLayoutEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [messages]);
  return (
    <div id="msgs" ref={box}>
      {messages.map((m) => <Message key={m.id} m={m} />)}
    </div>
  );
}

/** Folder scope silently shrinks every answer, so it sits above the input. */
function ScopeBar() {
  const ids = useStore((s) => s.scopeFolderIds);
  const names = useStore((s) => s.scopeNames);
  const unreachable = useStore((s) => s.unreachableScopeIds);
  if (!ids.length) return null;
  const stranded = ids.filter((id) => unreachable.includes(id)).length;
  const label = ids.map((id) => names[id] ?? `#${id}`).join(", ");
  return (
    <div className="scopebar">
      <span className="txt">
        asking only within {ids.length} folder{ids.length === 1 ? "" : "s"}: {label}
        {stranded ? ` — ${stranded} of them belongs to another session and answers nothing here` : ""}
      </span>
      <button className="sm" title="Search the whole space again." onClick={clearScope}>✕</button>
    </div>
  );
}

function useSessionNote() {
  return useStore((s) => (s.threadId != null ? s.sessionNotes[s.threadId] ?? "" : "").trim());
}

/**
 * The session-context toggle. Like the scope, a note silently changes every
 * answer, so once one is set the chip lights up and shows its first line.
 */
function ContextChip({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  const note = useSessionNote();
  const first = note.split("\n")[0];
  return (
    <button className={`chip${note ? " on" : ""}${open ? " open" : ""}`} onClick={onToggle}
      title={note
        ? `Session context (${note.length} chars) — click to edit:\n\n${note}`
        : "Give the model facts your documents don't have yet — a changed role, a recent event. " +
          "Kept for this session only, sent with every question and report, never added to the knowledge base."}>
      <span className="ico">✎</span>
      <span className="txt">{note ? `Context: ${first}${note.includes("\n") ? " …" : ""}` : "Session context"}</span>
    </button>
  );
}

function ContextPanel({ onClose }: { onClose: () => void }) {
  const note = useSessionNote();
  const [text, setText] = useState(note);
  return (
    <div className="ctxpanel">
      <textarea rows={4} maxLength={SESSION_CONTEXT_MAX} autoFocus value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Facts every answer in this session should take as true — e.g. “Since 08/2026, Nguyễn Văn A is Minister of Finance (no longer Deputy Minister).” Newer than your documents: where they disagree, this wins." />
      <div className="row">
        <span className="note">{text.length}/{SESSION_CONTEXT_MAX} · this session only, never saved to the knowledge base</span>
        <button className="sm danger" onClick={async () => {
          if (!note || await confirmAction({
            title: "Clear this session's context?",
            message: "Answers in this session stop taking it as true.",
            confirmLabel: "Clear", danger: true,
          })) {
            setSessionContext("");
            toast("Session context cleared.");
            onClose();
          }
        }}>Clear</button>
        <button className="sm" onClick={onClose}>Close</button>
        <button className="sm primary" onClick={() => {
          setSessionContext(text);
          toast(text.trim()
            ? "Session context saved — every question and report in this session now uses it."
            : "Session context cleared.");
          onClose();
        }}>Save</button>
      </div>
    </div>
  );
}

function Composer() {
  const busy = useStore((s) => s.busy);
  const threadId = useStore((s) => s.threadId);
  const [text, setText] = useState("");
  const [ctxOpen, setCtxOpen] = useState(false);
  const templates = useStore((s) => s.templates);
  const [tplMenu, setTplMenu] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);

  // Grow with the text, up to a cap.
  useLayoutEffect(() => {
    const q = input.current;
    if (!q) return;
    q.style.height = "auto";
    q.style.height = `${Math.min(q.scrollHeight, 200)}px`;
  }, [text]);
  useEffect(() => { if (!busy) input.current?.focus(); }, [busy]);
  // The note is per session: switching sessions closes the editor on the old one.
  useEffect(() => setCtxOpen(false), [threadId]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setCtxOpen(false); setTplMenu(false); } };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // The Report button: what is in the input becomes the report request. With
  // templates to choose from, a menu asks which (or none) first.
  const report = (tpl?: Template | null) => {
    const q = text.trim();
    if (busy) return;
    if (!q) {
      toast("Type what the report should cover in the chat box, then press Report.", "warn");
      input.current?.focus();
      return;
    }
    if (tpl === undefined && templates.length) { setTplMenu(true); return; }
    setTplMenu(false);
    setText("");
    guard(() => writeReport(q, tpl ?? null));
  };

  const submit = () => {
    const q = text.trim();
    if (!q || busy) return;
    setText("");
    guard(() => ask(q));
  };

  return (
    <footer>
      <div className="composer">
        <ScopeBar />
        {ctxOpen && <ContextPanel key={threadId} onClose={() => setCtxOpen(false)} />}
        <div className="inbox" onClick={(e) => { if (e.target === e.currentTarget) input.current?.focus(); }}>
          <textarea id="q" ref={input} rows={1} value={text}
            placeholder="Ask your knowledge base…"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }} />
          <div className="toolrow">
            <ContextChip open={ctxOpen} onToggle={() => setCtxOpen(!ctxOpen)} />
            <span className="hint">Enter to send · Shift+Enter for a newline</span>
            <div className="menu">
              <button className="ghost" disabled={busy} onClick={() => report()}
                title="Write a Markdown report from your documents on what the chat box says (inside the ticked folders, if any)">
                📄 Report
              </button>
              {tplMenu && (
                <div className="menu-list up" onMouseLeave={() => setTplMenu(false)}>
                  <div className="menu-head">Format like a template</div>
                  <button onClick={() => report(null)}>No template</button>
                  {templates.map((t) => (
                    <button key={t.id} onClick={() => report(t)}>t{t.id} · {t.name}</button>
                  ))}
                </div>
              )}
            </div>
            <button className="primary" disabled={busy || !text.trim()} onClick={submit}>Send</button>
          </div>
        </div>
      </div>
    </footer>
  );
}

/** Hides or shows the sidebar; Ctrl+B does the same. */
function SidebarToggle() {
  const hidden = useLayout((s) => s.sidebarHidden);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "b") {
        e.preventDefault();
        toggleSidebar();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  return (
    <button className="side-toggle" onClick={toggleSidebar}
      title={`${hidden ? "Show" : "Hide"} the sidebar (Ctrl+B)`} aria-label={hidden ? "Show sidebar" : "Hide sidebar"}>
      <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6">
        <rect x="2.5" y="3.5" width="15" height="13" rx="2.5" />
        <path d="M7.5 3.5v13" />
        {hidden ? <path d="m11 8.5 2 1.5-2 1.5" strokeLinecap="round" strokeLinejoin="round" />
          : <path d="m13 8.5-2 1.5 2 1.5" strokeLinecap="round" strokeLinejoin="round" />}
      </svg>
    </button>
  );
}

export function ChatView() {
  return (
    <main>
      <SidebarToggle />
      <Messages />
      <Composer />
    </main>
  );
}
