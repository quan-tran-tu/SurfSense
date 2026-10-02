/*
 * One tree for everything a question can read: the space's own folders, the
 * folders an admin granted to a group you are in (under the group's name) and —
 * for a system admin — every non-admin user's folders, each user shown as one
 * more folder holding theirs. The server builds the list from the grant
 * retrieval uses, so the tree can't show a folder a question can't read.
 *
 * Folders arrive in one request; a folder's files are fetched when it is first
 * opened — a large upload is thousands of rows nobody asked to see.
 */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { FolderDocument, FolderNode } from "../../api/types";
import { keys } from "../../queryClient";
import { setState, useStore } from "../../store";
import { confirmThen } from "../../ui/confirm";
import { ICON } from "../../ui/icons";
import { toast } from "../../ui/toast";
import { deleteFolder, demoteFolder, folderDocs, promoteFolder } from "../folders";
import { folderLabel, toggleScope } from "../scope";
import { refreshFolders } from "../../queryClient";
import { ShareDialog } from "./dialogs";

const openNodes = new Set<string>();   // expanded rows, kept across re-renders and refetches
export const byName = (a: { name: string }, b: { name: string }) => a.name.localeCompare(b.name);
const indent = (depth: number, extra = 0) => `${8 + depth * 14 + extra}px`;

/** A row button that acts without also toggling the row it sits in. */
export function ActBtn({ label, cls = "", title, onClick }:
  { label: string; cls?: string; title?: string; onClick: () => void }) {
  return (
    <button className={`sm act ${cls}`.trim()} title={title}
      onClick={(e) => { e.stopPropagation(); onClick(); }}>{label}</button>
  );
}

/**
 * A scope checkbox standing for one or more folder ids — checked when all of
 * them are in scope. It reads the scope from the store, so a folder ticked in one
 * place shows ticked everywhere it appears.
 */
export function ScopePick({ ids, names, title, disabled }:
  { ids: number[]; names: Record<number, string>; title: string; disabled?: boolean }) {
  const ticked = useStore((s) => ids.filter((id) => s.scopeFolderIds.includes(id)).length);
  const checked = ids.length > 0 && ticked === ids.length;
  // Some but not all of a group's or user's folders in scope: show it half-ticked.
  const box = useRef<HTMLInputElement>(null);
  useEffect(() => { if (box.current) box.current.indeterminate = ticked > 0 && !checked; }, [ticked, checked]);
  return (
    <input ref={box} type="checkbox" className="pick" title={title} disabled={disabled} checked={checked}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => toggleScope(ids, e.target.checked, names)} />
  );
}

const Note = ({ depth, children }: { depth: number; children: ReactNode }) =>
  <div className="empty" style={{ paddingLeft: indent(depth + 1, 18) }}>{children}</div>;

/**
 * A row that expands. `children` renders only while open, so whatever it fetches
 * is fetched on first open. `lead` goes before the name (a checkbox), `trail`
 * after it (status marks), and `actions` floats over the row's right end on
 * hover — taking no room, so every row's count lines up. Each must stop
 * propagation or it toggles the row.
 */
export function TreeRow({ nodeKey, depth, name, nameTitle, count, cls = "", lead, trail, actions, children }: {
  nodeKey: string; depth: number; name: string; nameTitle?: string; count?: number; cls?: string;
  lead?: ReactNode; trail?: ReactNode; actions?: ReactNode; children: () => ReactNode;
}) {
  const [open, setOpen] = useState(openNodes.has(nodeKey));
  const toggle = () => {
    if (open) openNodes.delete(nodeKey); else openNodes.add(nodeKey);
    setOpen(!open);
  };
  return (
    <div>
      <div className={`item ${cls}`.trim()} style={{ paddingLeft: indent(depth) }} onClick={toggle}>
        <span className="caret">{open ? "▾" : "▸"}</span>
        {lead}
        <span className="name" title={nameTitle}>{name}</span>
        {trail}
        {count ? <span className="count">{count}</span> : null}
        {actions && <span className="acts">{actions}</span>}
      </div>
      {open && <div>{children()}</div>}
    </div>
  );
}

function FileRow({ d, depth }: { d: FolderDocument; depth: number }) {
  const st = d.status?.state;
  return (
    <div className="item file" style={{ paddingLeft: indent(depth, 18) }} title="Open this document"
      onClick={() => setState({ source: { kind: "document", id: d.id, title: d.title } })}>
      <span className="name">{d.title}</span>
      {st && st !== "ready" && <span className={`badge ${st === "failed" ? "dead" : "off"}`}>{st}</span>}
    </div>
  );
}

/** A folder's subfolders, then its files (fetched now that it is open). */
export function FolderChildren({ f, depth, nodes }: { f: FolderNode; depth: number; nodes: FolderNode[] }) {
  const spaceId = useStore((s) => s.spaceId);
  const docs = useQuery({
    queryKey: keys.folderDocs(spaceId, f.id),
    queryFn: () => folderDocs(f.id),
    enabled: f.document_count > 0,
  });
  const subs = nodes.filter((n) => n.parent_id === f.id).sort(byName);
  if (f.document_count > 0 && docs.isPending) return <Note depth={depth - 1}>loading…</Note>;
  if (docs.isError) return <Note depth={depth - 1}>could not load: {docs.error.message}</Note>;
  const files = docs.data ?? [];
  if (!subs.length && !files.length) return <Note depth={depth - 1}>empty</Note>;
  return (
    <>
      {subs.map((n) => <FolderRow key={n.id} f={n} depth={depth} nodes={nodes} />)}
      {files.map((d) => <FileRow key={d.id} d={d} depth={depth} />)}
    </>
  );
}

export function FolderRow({ f, depth, nodes }: { f: FolderNode; depth: number; nodes: FolderNode[] }) {
  const threadId = useStore((s) => s.threadId);
  const [sharing, setSharing] = useState(false);
  const scoped = f.owner_thread_id != null;
  const mine = scoped && f.owner_thread_id === threadId;
  // Promote, share and delete act on a folder this space owns, at its root.
  // Everything else — subfolders, imports, users' folders — is read-only.
  const actionable = f.origin === "own" && f.parent_id == null;

  const lead = scoped && !mine
    // Another session's folder is hidden from retrieval here whatever the scope
    // says, so offering it would only produce a scope that answers nothing.
    ? <ScopePick ids={[f.id]} names={{ [f.id]: folderLabel(f) }} disabled
        title="Owned by another session — it can't answer questions here." />
    : <ScopePick ids={[f.id]} names={{ [f.id]: folderLabel(f) }}
        title="Tick to ask questions only inside this folder (and everything under it)." />;

  // A dot before the name: filled for this session's, hollow for another's.
  const mark = actionable && scoped && (
    <span className={`smark${mine ? " mine" : ""}`}
      title={mine ? "Session-only: just this chat session sees it." : `Session-only: belongs to session #${f.owner_thread_id}.`} />
  );

  // Promoted out of a session that still exists: ⤵ can put it back there.
  const from = f.promoted_from_thread_id;
  const actions = actionable && (
    <>
      {scoped ? (
        <>
          <ActBtn label={ICON.promote} title="Make this folder space-wide: every chat session will see it." onClick={() =>
            confirmThen({
              title: `Make "${f.name}" space-wide?`,
              message: "Every chat session will then see and search it. ⤵ can scope it back to this session later.",
              confirmLabel: "Make space-wide",
            }, () => promoteFolder(f))} />
        </>
      ) : (
        <>
          {from != null && (
            <ActBtn label={ICON.demote}
              title={`Make it session-only again: only ${from === threadId ? "this session" : `session #${from}`}, where it was uploaded, will see it.`}
              onClick={() => confirmThen({
                title: `Scope "${f.name}" back to ${from === threadId ? "this session" : `session #${from}`}?`,
                message: "Your other chat sessions stop seeing it. A folder that is shared or granted to a group must be revoked first.",
                confirmLabel: "Make session-only",
              }, () => demoteFolder(f))} />
          )}
          {/* The backend refuses to share a session-scoped folder, so only a
              space-wide one gets the button. */}
          <ActBtn label="Share" onClick={() => setSharing(true)} />
        </>
      )}
      <ActBtn label={ICON.remove} cls="danger" title="Delete this folder and its documents" onClick={() =>
        confirmThen({
          title: `Delete "${f.name}"?`,
          message: "The folder and all of its documents are deleted. Anyone reading it through a share or a group loses it too. This can't be undone.",
          confirmLabel: "Delete", danger: true,
        }, async () => { await deleteFolder(f.id); toast(`Removed "${f.name}".`); await refreshFolders(); })} />
    </>
  );

  return (
    <>
      <TreeRow nodeKey={`f:${f.id}`} depth={depth} name={f.name} count={f.document_count}
        nameTitle={f.origin !== "own" ? `${folderLabel(f)} — read-only` : undefined}
        lead={<>{lead}{mark}</>} actions={actions}>
        {() => <FolderChildren f={f} depth={depth + 1} nodes={nodes} />}
      </TreeRow>
      {sharing && <ShareDialog folderId={f.id} path={f.name} onClose={() => setSharing(false)} />}
    </>
  );
}

/**
 * A grouping row: one more folder holding several read-only roots, with a
 * checkbox that scopes a question to all of them at once. `user` is a user as a
 * system admin sees them; `group` is a user group you belong to, holding the
 * folders an admin granted it. The sub-fold it sits in already says which.
 */
export function GroupingRow({ kind, name, roots, nodes }:
  { kind: "user" | "group"; name: string; roots: FolderNode[]; nodes: FolderNode[] }) {
  const owned = kind === "user" ? `${name}'s folders` : `the ${name} group's folders`;
  return (
    <TreeRow nodeKey={`${kind}:${name}`} depth={0} name={name} cls="group" nameTitle={`${owned} — read-only`}
      lead={<ScopePick ids={roots.map((r) => r.id)}
        names={Object.fromEntries(roots.map((r) => [r.id, folderLabel(r)]))}
        title={`Tick to ask questions only inside ${owned}.`} />}
>
      {() => roots.map((r) => <FolderRow key={r.id} f={r} depth={1} nodes={nodes} />)}
    </TreeRow>
  );
}
