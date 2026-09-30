import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Markdown } from "../../lib/Markdown";
import type { Template } from "../../api/types";
import { useStore, type ChatMessage } from "../../store";
import { toggleSidebar, useLayout } from "../../ui/layout";
import { guard, toast } from "../../ui/toast";
import { clearScope } from "../scope";
import { SESSION_CONTEXT_MAX, setSessionContext } from "../sessionContext";
import { ask } from "./ask";
import { runCommand } from "./commands";
import { reportCommand } from "./reports";

function Message({ m }: { m: ChatMessage }) {
  const model = useStore((s) => s.model);
  if (m.role === "system") {
    return (
      <div className="msg system">
        <div className="bubble sys">{m.text}</div>
        {m.links && (
          <div className="msglinks">
            {m.links.map((l) => <a key={l.href} href={l.href} target="_blank" rel="noopener">{l.label}</a>)}
          </div>
        )}
      </div>
    );
  }
  return (
    <div className={`msg ${m.role}`}>
      <div className="who">{m.role === "user" ? "You" : model}</div>
      <div className={`bubble${m.streaming ? " cursor" : ""}`}>
        {m.role === "user" ? m.text : <Markdown text={m.text} msgId={m.id} />}
      </div>
      <div className={`status ${m.statusKind ?? ""}`}>{m.status}</div>
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
        <button className="sm danger" onClick={() => {
          if (!note || confirm("Clear this session's context?")) {
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
      toast("Gõ vào ô chat nội dung báo cáo cần viết, rồi bấm Report.", "warn");
      input.current?.focus();
      return;
    }
    if (tpl === undefined && templates.length) { setTplMenu(true); return; }
    setTplMenu(false);
    setText("");
    guard(() => reportCommand(q, null, tpl ?? null));
  };

  const submit = () => {
    const q = text.trim();
    if (!q || busy) return;
    setText("");
    // A leading slash is a command (/report, /export, …); everything else is a question.
    guard(() => (q[0] === "/" ? runCommand(q) : ask(q)));
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
                title="Viết báo cáo Markdown từ kho tài liệu, theo nội dung trong ô chat (trong phạm vi folder đang chọn)">
                📄 Report
              </button>
              {tplMenu && (
                <div className="menu-list up" onMouseLeave={() => setTplMenu(false)}>
                  <div className="menu-head">Định dạng theo mẫu</div>
                  <button onClick={() => report(null)}>Không dùng mẫu</button>
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
