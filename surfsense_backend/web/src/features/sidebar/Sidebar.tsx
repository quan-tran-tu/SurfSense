import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { signOut } from "../../api/client";
import type { FolderLink, FolderNode } from "../../api/types";
import { keys } from "../../queryClient";
import { setState, useStore } from "../../store";
import { setSidebarWidth, toggleSection, useLayout } from "../../ui/layout";
import { guard, toast } from "../../ui/toast";
import { addSystemNote } from "../chat/messages";
import { importFolder, listImports, loadTree, removeImport, unshareFolder, useIngest } from "../folders";
import { clearScope, folderLabel, otherSession, pruneScope, setScope } from "../scope";
import { createSession, deleteSession, listThreads, openThread } from "../session";
import { addTemplates, removeTemplate } from "../templates";
import { UploadDialog } from "./dialogs";
import { ActBtn, byName, FolderChildren, FolderRow, GroupingRow, ScopePick, TreeRow } from "./FolderTree";

/* ----------------------------------------------------------------- section */

/**
 * A sidebar section whose body folds away under its heading. Buttons in `actions`
 * sit in the heading and act without folding it.
 */
function Section({ id, title, actions, children }:
  { id: string; title: ReactNode; actions?: ReactNode; children: ReactNode }) {
  const collapsed = useLayout((s) => !!s.collapsed[id]);
  return (
    <section className={collapsed ? "folded" : undefined}>
      <h2 className="fold" onClick={() => toggleSection(id)} title={collapsed ? "Show" : "Hide"}>
        <span><span className="caret">{collapsed ? "▸" : "▾"}</span>{title}</span>
        {actions && <span className="hacts" onClick={(e) => e.stopPropagation()}>{actions}</span>}
      </h2>
      {!collapsed && children}
    </section>
  );
}

/* ---------------------------------------------------------------- sessions */

function Sessions() {
  const spaceId = useStore((s) => s.spaceId);
  const threadId = useStore((s) => s.threadId);
  const threads = useQuery({ queryKey: keys.threads(spaceId), queryFn: listThreads });
  const [title, setTitle] = useState("");
  const start = () => { const t = title.trim(); setTitle(""); guard(() => createSession(t)); };

  return (
    <Section id="sessions" title="Session">
      <div>
        {threads.data && !threads.data.length && <div className="empty">no sessions yet</div>}
        {threads.data?.map((t) => (
          <div key={t.id} className={`item${t.id === threadId ? " active" : ""}`}
            onClick={() => t.id !== threadId && guard(() => openThread(t))}>
            <span className="name">{t.title}</span>
            <ActBtn label="✕" cls="danger" onClick={() => {
              if (confirm(`Delete session "${t.title}" and all of its messages?`)) guard(() => deleteSession(t));
            }} />
          </div>
        ))}
      </div>
      <div className="stack" style={{ marginTop: 8 }}>
        <input placeholder="new session (name optional)…" value={title}
          onChange={(e) => setTitle(e.target.value)} onKeyDown={(e) => e.key === "Enter" && start()} />
        <button className="sm" onClick={start}>Start</button>
      </div>
    </Section>
  );
}

/* ----------------------------------------------------------------- folders */

/** Roots of a foreign origin, bucketed by the name they hang under. */
function bucket(nodes: FolderNode[], origin: FolderNode["origin"], key: "group_name" | "owner_email") {
  const map = new Map<string, FolderNode[]>();
  for (const n of nodes.filter((n) => n.origin === origin && n.parent_id == null).sort(byName)) {
    const k = n[key] ?? "";
    if (!map.has(k)) map.set(k, []);
    map.get(k)!.push(n);
  }
  return map;
}

/**
 * Every root a question here can read, with its scope label: own folders this
 * session may use, group and user roots, and live imports. Ticking them all is
 * the same search as ticking none — the button just says so out loud.
 */
function pickableRoots(nodes: FolderNode[], links: FolderLink[], threadId: number | null) {
  const names: Record<number, string> = {};
  for (const n of nodes) {
    if (n.parent_id == null && n.origin !== "linked" && !otherSession(n, threadId)) names[n.id] = folderLabel(n);
  }
  for (const l of links) if (l.live !== false) names[l.source_folder_id] = `${l.folder_name} (imported)`;
  return names;
}

function PickAll({ nodes, links }: { nodes: FolderNode[] | undefined; links: FolderLink[] | undefined }) {
  const threadId = useStore((s) => s.threadId);
  const scope = useStore((s) => s.scopeFolderIds);
  if (!nodes || !links) return null;
  const names = pickableRoots(nodes, links, threadId);
  const ids = Object.keys(names).map(Number);
  if (!ids.length) return null;
  const all = ids.every((id) => scope.includes(id));
  return all
    ? <button className="sm" title="Untick every folder: questions search the whole space again." onClick={clearScope}>None</button>
    : <button className="sm" title="Tick every folder you can search." onClick={() => setScope(ids, names)}>All</button>;
}

function Folders({ nodes, links }: { nodes: FolderNode[] | undefined; links: FolderLink[] | undefined }) {
  const picker = useRef<HTMLInputElement>(null);
  const [picked, setPicked] = useState<File[] | null>(null);
  const progress = useIngest();

  const own = (nodes ?? []).filter((n) => n.origin === "own" && n.parent_id == null).sort(byName);
  const groups = bucket(nodes ?? [], "group", "group_name");
  const users = bucket(nodes ?? [], "user", "owner_email");

  return (
    <Section id="folders" title="Folders" actions={<>
      <PickAll nodes={nodes} links={links} />
      <button className="sm" onClick={() => picker.current?.click()}>+ Add</button>
    </>}>
      <input ref={picker} type="file" className="hide" multiple
        {...{ webkitdirectory: "", directory: "" }}
        onChange={(e) => {
          // input.files is LIVE: clearing the input (so re-picking the same folder
          // fires again) empties it. Copy the entries out first.
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (files.length) setPicked(files);
        }} />
      {picked && <UploadDialog files={picked} onClose={() => setPicked(null)} />}
      <div>
        {nodes && !own.length && !groups.size && !users.size && <div className="empty">no folders indexed</div>}
        {own.map((f) => <FolderRow key={f.id} f={f} depth={0} nodes={nodes!} />)}
        {[...groups.keys()].sort().map((name) =>
          <GroupingRow key={`g:${name}`} kind="group" name={name} roots={groups.get(name)!} nodes={nodes!} />)}
        {[...users.keys()].sort().map((email) =>
          <GroupingRow key={`u:${email}`} kind="user" name={email} roots={users.get(email)!} nodes={nodes!} />)}
      </div>
      {progress.text != null && (
        <div>
          <div className="note">{progress.text}</div>
          <div className="bar"><div style={{ width: `${Math.round(progress.frac * 100)}%` }} /></div>
        </div>
      )}
    </Section>
  );
}

/* --------------------------------------------------------------- templates */

function Templates() {
  const templates = useStore((s) => s.templates);
  const picker = useRef<HTMLInputElement>(null);
  return (
    <Section id="templates" title="Report templates"
      actions={<button className="sm" onClick={() => picker.current?.click()}>+ Add</button>}>
      {/* No accept filter: the server converter decides what it can read. */}
      <input ref={picker} type="file" multiple className="hide" onChange={(e) => {
        const files = Array.from(e.target.files ?? []);
        e.target.value = "";
        if (files.length) guard(() => addTemplates(files));
      }} />
      <div>
        {!templates.length && <div className="empty">none — upload a sample report (.docx preferred)</div>}
        {templates.map((t) => (
          <div key={t.id} className="item" title="Click to preview the section outline" onClick={() => addSystemNote(
            `Template t${t.id} — ${t.name}\n\n` +
            (t.outline.length
              ? `Sections:\n${t.outline.join("\n")}`
              : "No headings detected — this template is used as a style exemplar only.") +
            `\n\nUse it: /report t${t.id} <query>`)}>
            <span className="name">t{t.id} · {t.name}</span>
            <ActBtn label="✕" cls="danger" onClick={() => {
              if (confirm(`Remove template t${t.id} "${t.name}"?`)) removeTemplate(t.id);
            }} />
          </div>
        ))}
      </div>
    </Section>
  );
}

/* ----------------------------------------------------------------- imports */

function Imports({ nodes, links }: { nodes: FolderNode[] | undefined; links: FolderLink[] | undefined }) {
  const [token, setToken] = useState("");
  const doImport = () => { const t = token.trim(); if (!t) return; setToken(""); guard(() => importFolder(t)); };

  return (
    <Section id="imports" title={<>Imported <span className="ro">read-only</span></>}>
      <div>
        {links && !links.length && <div className="empty">nothing imported</div>}
        {links?.map((link) => {
          // A revoked share stops resolving, so the tree no longer holds its folder
          // and the row expands to nothing — the "revoked" label says why.
          const node = nodes?.find((n) => n.id === link.source_folder_id);
          return (
            <TreeRow key={link.id} nodeKey={`l:${link.id}`} depth={0} name={link.folder_name}
              count={node?.document_count}
              lead={<ScopePick ids={[link.source_folder_id]}
                names={{ [link.source_folder_id]: `${link.folder_name} (imported)` }}
                title="Tick to ask questions only inside this imported folder." />}
              trail={<>
                {link.live === false && (
                  <span className="dead" title="The owner revoked this share or it expired, so it no longer answers questions.">revoked</span>
                )}
                <ActBtn label="✕" cls="danger" onClick={() => {
                  if (confirm(`Remove the imported folder "${link.folder_name}"?\n\nOnly your link is removed — the owner's documents are untouched, and the same token can import it again.`)) {
                    guard(() => removeImport(link));
                  }
                }} />
              </>}>
              {() => (node ? <FolderChildren f={node} depth={1} nodes={nodes!} /> : <div className="empty">empty</div>)}
            </TreeRow>
          );
        })}
      </div>
      <div className="stack" style={{ marginTop: 8 }}>
        <input placeholder="paste a share token…" value={token}
          onChange={(e) => setToken(e.target.value)} onKeyDown={(e) => e.key === "Enter" && doImport()} />
        <button className="sm" onClick={doImport}>Import</button>
      </div>
    </Section>
  );
}

/* ------------------------------------------------------------------ shares */

const day = (iso: string) => new Date(iso).toLocaleDateString();

function Shares() {
  const shares = useStore((s) => s.shares);
  return (
    <Section id="shares" title="Shared by you">
      {!shares.length && <div className="empty">nothing shared</div>}
      {shares.map((s) => {
        const expired = !!s.expiresAt && Date.parse(s.expiresAt) <= Date.now();
        return (
          <div key={s.token}>
            <div className="item" style={{ cursor: "default" }}>
              <span className="name mono">{s.path}</span>
              <button className="sm" onClick={() => navigator.clipboard.writeText(s.token).then(() => toast("Token copied."))}>Copy</button>
              <button className="sm danger" onClick={() => guard(() => unshareFolder(s.token))}>Revoke</button>
            </div>
            <div className="token mono">{s.token}</div>
            <div className={`note${expired ? " warn" : ""}`} style={{ marginTop: 0 }}>
              {!s.expiresAt ? "never expires" : expired ? `expired ${day(s.expiresAt)}` : `expires ${day(s.expiresAt)}`}
            </div>
          </div>
        );
      })}
    </Section>
  );
}

/* ----------------------------------------------------------------- resizer */

/** Drag the sidebar's right edge to resize it; double-click resets it. */
function Resizer() {
  const start = (e: ReactPointerEvent) => {
    e.preventDefault();
    const x0 = e.clientX, w0 = useLayout.getState().sidebarWidth;
    const move = (ev: PointerEvent) => setSidebarWidth(w0 + ev.clientX - x0);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.classList.remove("resizing");
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    document.body.classList.add("resizing");
  };
  return <div className="resizer" onPointerDown={start} onDoubleClick={() => setSidebarWidth(300)}
    title="Drag to resize · double-click to reset" />;
}

/* ----------------------------------------------------------------- sidebar */

export function Sidebar() {
  const spaceId = useStore((s) => s.spaceId);
  const threadId = useStore((s) => s.threadId);
  const email = useStore((s) => s.email);
  const isAdmin = useStore((s) => s.isAdmin);
  const width = useLayout((s) => s.sidebarWidth);
  const tree = useQuery({ queryKey: keys.tree(spaceId), queryFn: loadTree });
  const links = useQuery({ queryKey: keys.imports(spaceId), queryFn: listImports });

  // Drop deleted folders from the scope once both lists are in, and note which
  // in-scope folders belong to another session (they answer nothing here).
  useEffect(() => {
    if (!tree.data || !links.data) return;
    pruneScope(new Set([...tree.data.map((n) => n.id), ...links.data.map((l) => l.source_folder_id)]));
  }, [tree.data, links.data]);
  useEffect(() => {
    setState({
      unreachableScopeIds: (tree.data ?? [])
        .filter((n) => otherSession(n, threadId)).map((n) => n.id),
    });
  }, [tree.data, threadId]);

  return (
    <aside id="sidebar" style={{ width }}>
      <div className="side-scroll">
        <Sessions />
        <Folders nodes={tree.data} links={links.data} />
        <Imports nodes={tree.data} links={links.data} />
        <Shares />
        {/* Last: templates matter only when writing a report, the rest every question. */}
        <Templates />
      </div>
      <div className="side-foot">
        <div className="note mono" style={{ whiteSpace: "pre-line", marginTop: 0 }}>{email}</div>
        {isAdmin && (
          <button id="adminBtn" className="sm" onClick={() => {
            history.replaceState(null, "", "#admin");
            setState({ adminOpen: true });
          }}>Admin</button>
        )}
        <button className="sm" style={{ marginTop: 8, width: "100%" }} onClick={() => guard(signOut)}>Sign out</button>
      </div>
      <Resizer />
    </aside>
  );
}
