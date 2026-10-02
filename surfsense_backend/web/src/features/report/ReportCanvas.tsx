/*
 * One report, full window: its Markdown source beside a live preview, its other
 * versions (the /revise chain) a pick away, save and export on the toolbar.
 *
 * Saving overwrites the version on screen (PUT /reports/{id}/content). Export
 * renders what the server holds, so unsaved edits are saved first. Switching
 * versions or leaving with unsaved edits asks before throwing them away.
 */
import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { apiJson } from "../../api/client";
import { Markdown } from "../../lib/Markdown";
import { queryClient } from "../../queryClient";
import { confirmAction } from "../../ui/confirm";
import { guard, toast } from "../../ui/toast";
import { downloadReport } from "../chat/reports";
import { reportHref } from "./route";

// CodeMirror is loaded only when a canvas opens; the chat page never pays for it.
const MarkdownEditor = lazy(() => import("./MarkdownEditor"));

interface ReportContent {
  id: number;
  title: string;
  content: string | null;
  content_type: string;
  versions: { id: number; created_at: string }[];
}

const EXPORTS: { fmt: string; label: string }[] = [
  { fmt: "pdf", label: "PDF" },
  { fmt: "docx", label: "Word (.docx)" },
  { fmt: "odt", label: "OpenDocument (.odt)" },
  { fmt: "html", label: "HTML" },
  { fmt: "md", label: "Markdown (.md)" },
  { fmt: "plain", label: "Plain text (.txt)" },
];

const contentKey = (id: number) => ["reportContent", id] as const;

type Mode = "edit" | "view";

export function ReportCanvas({ id }: { id: number }) {
  const q = useQuery({
    queryKey: contentKey(id),
    queryFn: () => apiJson<ReportContent>("GET", `/api/v1/reports/${id}/content`),
    // Coming back from the chat tab after a /revise shows the new version.
    refetchOnWindowFocus: true,
    staleTime: 0,
  });
  const saved = q.data?.content ?? "";
  const [draft, setDraft] = useState<string | null>(null);   // null = no edits
  const [mode, setMode] = useState<Mode>("edit");
  const [saving, setSaving] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const preview = useRef<HTMLDivElement>(null);

  const text = draft ?? saved;
  const dirty = draft !== null && draft !== saved;

  // A different report (version switch) starts clean.
  useEffect(() => setDraft(null), [id]);

  useEffect(() => {
    if (q.data) document.title = `${dirty ? "● " : ""}${q.data.title} · Report #${id}`;
  }, [q.data, id, dirty]);

  // Closing the tab or reloading with unsaved edits asks first.
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const save = useCallback(async (): Promise<boolean> => {
    if (!dirty || saving) return true;
    setSaving(true);
    try {
      const res = await apiJson<ReportContent>("PUT", `/api/v1/reports/${id}/content`, { json: { content: draft } });
      queryClient.setQueryData(contentKey(id), res);
      setDraft(null);
      toast("Report saved.");
      return true;
    } catch (e) {
      toast(`Save failed: ${e instanceof Error ? e.message : e}`, "err", 10000);
      return false;
    } finally {
      setSaving(false);
    }
  }, [dirty, saving, id, draft]);

  // Ctrl+S anywhere on the page, not only inside the editor.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); void save(); }
      if (e.key === "Escape") setExportOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [save]);

  const switchVersion = async (to: number) => {
    if (to === id) return;
    if (dirty && !await confirmAction({
      title: "Discard unsaved changes?",
      message: "This version has unsaved changes. Switching versions discards them.",
      confirmLabel: "Discard and switch", danger: true,
    })) return;
    setDraft(null);
    location.hash = reportHref(to);
  };

  const doExport = (fmt: string) => guard(async () => {
    setExportOpen(false);
    // Export renders the server's copy, so it must hold what is on screen.
    if (dirty) {
      if (!await confirmAction({
        title: "Save, then export?",
        message: "Export uses the copy saved on the server. Save your changes and export?",
        confirmLabel: "Save and export",
      })) return;
      if (!(await save())) return;
    }
    await downloadReport(id, fmt);
  });

  // Keep the preview roughly level with the source while scrolling the editor.
  const syncPreview = (fraction: number) => {
    const p = preview.current;
    if (p) p.scrollTop = fraction * (p.scrollHeight - p.clientHeight);
  };

  if (q.isPending) return <div className="canvas-msg">Loading report #{id}…</div>;
  if (q.isError) {
    return (
      <div className="canvas-msg">
        Could not open report #{id}: {q.error.message}
        <div><a href="./">← Back to chat</a></div>
      </div>
    );
  }

  const r = q.data;
  const versions = r.versions.length ? r.versions : [{ id: r.id, created_at: "" }];
  const vIndex = versions.findIndex((v) => v.id === id);

  if (r.content_type !== "markdown") {
    return <div className="canvas-msg">Report #{id} is not Markdown ({r.content_type}), so it can't be edited here.</div>;
  }

  return (
    <div id="canvas">
      <header className="cbar">
        <a className="back" href="./" title="Open the chat in this tab">← Chat</a>
        <div className="ctitle">
          <strong>{r.title}</strong>
          <span className="sub"> · Report #{id}</span>
        </div>
        {versions.length > 1 && (
          <select value={id} onChange={(e) => switchVersion(Number(e.target.value))} title="Version (the original and each /revise)">
            {versions.map((v, i) => (
              <option key={v.id} value={v.id}>
                v{i + 1}{v.created_at ? ` · ${new Date(v.created_at).toLocaleString()}` : ""}{i === versions.length - 1 ? " (latest)" : ""}
              </option>
            ))}
          </select>
        )}
        {versions.length > 1 && vIndex !== versions.length - 1 && <span className="badge off">older version</span>}
        <span className="grow" />
        {dirty && <span className="unsaved">● unsaved</span>}
        <div className="seg">
          <button className={mode === "edit" ? "on" : ""} onClick={() => setMode("edit")}>Edit</button>
          <button className={mode === "view" ? "on" : ""} onClick={() => setMode("view")}>View</button>
        </div>
        <button className="primary" disabled={!dirty || saving} onClick={() => void save()} title="Ctrl+S">
          {saving ? "Saving…" : "Save"}
        </button>
        <div className="menu">
          <button onClick={() => setExportOpen(!exportOpen)}>Export ▾</button>
          {exportOpen && (
            <div className="menu-list" onMouseLeave={() => setExportOpen(false)}>
              {EXPORTS.map((x) => <button key={x.fmt} onClick={() => doExport(x.fmt)}>{x.label}</button>)}
            </div>
          )}
        </div>
      </header>
      <div className={`cbody ${mode}`}>
        {mode === "edit" && (
          <div className="cpane source">
            <Suspense fallback={<div className="canvas-msg">Loading the editor…</div>}>
              <MarkdownEditor value={text} onChange={setDraft} onSave={() => void save()} onScroll={syncPreview} />
            </Suspense>
          </div>
        )}
        <div className="cpane preview" ref={preview}>
          <div className="doc bubble"><Markdown text={text} /></div>
        </div>
      </div>
    </div>
  );
}
