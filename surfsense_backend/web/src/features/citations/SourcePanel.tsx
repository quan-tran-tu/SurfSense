import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { ApiError, apiJson } from "../../api/client";
import type { Chunk } from "../../api/types";
import { setState, useStore, type SourceView } from "../../store";

interface ChunkDoc { id: number; title?: string; chunks?: Chunk[] }
interface FullDoc { content?: string }

const errText = (e: unknown, gone: string, what: string) =>
  e instanceof ApiError && e.status === 404 ? gone : `Could not load ${what}: ${e instanceof Error ? e.message : e}`;

/**
 * A cited chunk resolved to its document, shown in context — a window of chunks
 * either side, with the cited one highlighted.
 */
function ChunkBody({ view }: { view: Extract<SourceView, { kind: "chunk" }> }) {
  const q = useQuery({
    queryKey: ["chunk", view.chunkId],
    queryFn: () => apiJson<ChunkDoc>("GET", `/api/v1/documents/by-chunk/${view.chunkId}?chunk_window=3`),
  });
  const hit = useRef<HTMLDivElement>(null);
  useEffect(() => { hit.current?.scrollIntoView({ block: "center" }); }, [q.data]);

  if (q.isPending) return <>Loading…</>;
  if (q.isError) {
    return <>{errText(q.error, `That passage is gone — chunk ${view.chunkId} no longer exists (the document was probably re-indexed or deleted).`, "the source")}</>;
  }
  return (
    <>
      {(q.data.chunks ?? []).map((c) => {
        const isHit = c.id === Number(view.chunkId);
        return <div key={c.id} ref={isHit ? hit : undefined} className={`chunk${isHit ? " hit" : ""}`}>{c.content}</div>;
      })}
    </>
  );
}

/**
 * A document picked in the folder tree, in the same panel a citation opens. The
 * server falls back to the link grant, so imported and admin-visible files open too.
 */
function DocumentBody({ id }: { id: number }) {
  const q = useQuery({
    queryKey: ["document", id],
    queryFn: () => apiJson<FullDoc>("GET", `/api/v1/documents/${id}`),
  });
  if (q.isPending) return <>Loading…</>;
  if (q.isError) return <>{errText(q.error, "That document is gone — it was probably deleted or re-indexed.", "the document")}</>;
  return <div className="chunk hit">{q.data?.content?.trim() || "No text was extracted from this document."}</div>;
}

function useTitle(view: SourceView) {
  const chunkDoc = useQuery({
    queryKey: ["chunk", view.kind === "chunk" ? view.chunkId : ""],
    queryFn: () => apiJson<ChunkDoc>("GET", `/api/v1/documents/by-chunk/${(view as { chunkId: string }).chunkId}?chunk_window=3`),
    enabled: view.kind === "chunk",
  });
  if (view.kind === "document") return view.title;
  return chunkDoc.data ? chunkDoc.data.title || `Document #${chunkDoc.data.id}` : `Source ${view.ordinal}`;
}

export function SourcePanel() {
  const view = useStore((s) => s.source);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setState({ source: null }); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  if (!view) return null;
  return <Panel view={view} />;
}

function Panel({ view }: { view: SourceView }) {
  const title = useTitle(view);
  return (
    <aside id="cite">
      <header>
        <span className="t">{title}</span>
        <button className="sm" onClick={() => setState({ source: null })}>✕</button>
      </header>
      <div className="body">
        {view.kind === "chunk" ? <ChunkBody key={view.chunkId} view={view} /> : <DocumentBody key={view.id} id={view.id} />}
      </div>
    </aside>
  );
}
