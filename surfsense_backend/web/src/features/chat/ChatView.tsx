import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Markdown } from "../../lib/Markdown";
import type { Template } from "../../api/types";
import { useStore, type ChatMessage } from "../../store";
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

/** Like the scope, a session note silently changes every answer, so it is announced. */
function ContextBar({ onEdit }: { onEdit: () => void }) {
  const note = useSessionNote();
  if (!note) return null;
  const first = note.split("\n")[0];
  return (
    <div className="scopebar ctx" title={note}>
      <span className="txt">session context: {first}{note.includes("\n") ? " …" : ""} ({note.length} chars)</span>
      <button className="sm" onClick={onEdit}>Edit</button>
    </div>
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
  const spaceId = useStore((s) => s.spaceId);
  const threadId = useStore((s) => s.threadId);
  const note = useSessionNote();
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
    q.style.height = `${Math.min(q.scrollHeight, 180)}px`;
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
        <ContextBar onEdit={() => setCtxOpen(true)} />
        {ctxOpen && <ContextPanel key={threadId} onClose={() => setCtxOpen(false)} />}
        <div className="box">
          <textarea id="q" ref={input} rows={1} value={text}
            placeholder="Ask your knowledge base…  (Enter to send · Shift+Enter for a newline · 📄 Report writes a report on it)"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }} />
          <button className="primary" disabled={busy} onClick={submit}>Send</button>
          <div className="menu">
            <button disabled={busy} onClick={() => report()}
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
        </div>
        <div className="opts">
          <button className={`link${note ? " on" : ""}`} onClick={() => setCtxOpen(!ctxOpen)}
            title="Give the model facts your documents don't have yet — a changed role, a recent event. Kept for this session only, sent with every question and report, never added to the knowledge base.">
            Session context
          </button>
          <span className="mono">space #{spaceId} · thread #{threadId}</span>
        </div>
      </div>
    </footer>
  );
}

export function ChatView() {
  return (
    <main>
      <Messages />
      <Composer />
    </main>
  );
}
