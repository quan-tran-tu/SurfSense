import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { signOut } from "../../api/client";
import type { FolderLink, FolderNode } from "../../api/types";
import { keys } from "../../queryClient";
import { setState, useStore } from "../../store";
import { setSidebarWidth, toggleSection, useCollapsed, useLayout } from "../../ui/layout";
import { confirmThen } from "../../ui/confirm";
import { ICON } from "../../ui/icons";
import { guard } from "../../ui/toast";
import { addSystemNote } from "../chat/messages";
import { EventsDialog, useEvents } from "../events";
import { importFolder, listImports, listShares, loadTree, removeImport, unshareFolder, useIngest } from "../folders";
import { clearScope, folderLabel, otherSession, pruneScope, setScope } from "../scope";
import { createSession, deleteSession, listThreads, openThread } from "../session";
import { addTemplates, removeTemplate } from "../templates";
import { TokenLine, UploadDialog } from "./dialogs";
import { ReportList } from "./Reports";
import { ActBtn, byName, FolderChildren, FolderRow, GroupingRow, ScopePick, TreeRow } from "./FolderTree";

/* ----------------------------------------------------------------- section */

/**
 * A sidebar section whose body folds away under its heading. Buttons in `actions`
 * sit in the heading and act without folding it.
 */
function Section({ id, title, actions, children }:
  { id: string; title: ReactNode; actions?: ReactNode; children: ReactNode }) {
  const collapsed = useCollapsed(id);
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
            <ActBtn label={ICON.remove} cls="danger" title="Delete this session" onClick={() =>
              confirmThen({
                title: `Delete session "${t.title}"?`,
                message: "All of its messages are deleted. Folders uploaded for this session only become visible to every session.",
                confirmLabel: "Delete", danger: true,
              }, () => deleteSession(t))} />
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

/**
 * A fold inside the Folders section — your own, your groups', other users'. An
 * admin can have many of each, so each kind folds away on its own; `byDefault`
 * folds the users' list until opened, since it grows with every account.
 */
function SubFold({ id, title, count, byDefault = false, children }:
  { id: string; title: string; count: number; byDefault?: boolean; children: ReactNode }) {
  const collapsed = useCollapsed(id, byDefault);
  return (
    <div className="subfold">
      <div className="subhead" onClick={() => toggleSection(id, byDefault)} title={collapsed ? "Show" : "Hide"}>
        <span className="caret">{collapsed ? "▸" : "▾"}</span>
        <span className="name">{title}</span>
        <span className="count">{count}</span>
      </div>
      {!collapsed && children}
    </div>
  );
}

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

  const isAdmin = useStore((s) => s.isAdmin);
  const own = (nodes ?? []).filter((n) => n.origin === "own" && n.parent_id == null).sort(byName);
  // Admins are never group members — they already read every user's folders.
  const groups = isAdmin ? new Map<string, FolderNode[]>() : bucket(nodes ?? [], "group", "group_name");
  const users = bucket(nodes ?? [], "user", "owner_email");
  const ownRows = own.map((f) => <FolderRow key={f.id} f={f} depth={0} nodes={nodes!} />);

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
        {/* Only your own folders: no heading needed over them. */}
        {!groups.size && !users.size ? ownRows : <>
          <SubFold id="folders:own" title="My folders" count={own.length}>
            {own.length ? ownRows : <div className="empty">none yet — + Add uploads one</div>}
          </SubFold>
          {groups.size > 0 && (
            <SubFold id="folders:groups" title="Groups" count={groups.size}>
              {[...groups.keys()].sort().map((name) =>
                <GroupingRow key={`g:${name}`} kind="group" name={name} roots={groups.get(name)!} nodes={nodes!} />)}
            </SubFold>
          )}
          {users.size > 0 && (
            <SubFold id="folders:users" title="Users" count={users.size} byDefault>
              {[...users.keys()].sort().map((email) =>
                <GroupingRow key={`u:${email}`} kind="user" name={email} roots={users.get(email)!} nodes={nodes!} />)}
            </SubFold>
          )}
        </>}
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
            <ActBtn label={ICON.remove} cls="danger" title="Remove this template" onClick={() =>
              confirmThen({
                title: `Remove template t${t.id} "${t.name}"?`,
                message: "It is kept only in this browser, so it can't be restored — upload the file again to bring it back.",
                confirmLabel: "Remove", danger: true,
              }, () => removeTemplate(t.id))} />
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
                  <span className="dead" title={link.state === "expired"
                    ? "The share's expiry passed, so it no longer answers questions."
                    : "The owner revoked this share, so it no longer answers questions."}>{link.state}</span>
                )}
                <ActBtn label={ICON.remove} cls="danger" title="Remove this import" onClick={() =>
                  confirmThen({
                    title: `Remove the imported folder "${link.folder_name}"?`,
                    message: "Only your link is removed — the owner's documents are untouched, and a live token can import it again.",
                    confirmLabel: "Remove", danger: true,
                  }, () => removeImport(link))} />
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

/**
 * The tokens you minted and haven't revoked, read from the server — so they
 * survive a new browser, and the same folder needn't be shared twice.
 */
function Shares() {
  const spaceId = useStore((s) => s.spaceId);
  const shares = useQuery({ queryKey: keys.shares(spaceId), queryFn: listShares });
  const list = shares.data ?? [];
  return (
    <Section id="shares" title="Shared by you">
      {shares.isPending && <div className="empty">loading…</div>}
      {shares.data && !list.length && <div className="empty">nothing shared — Share on a folder makes a token</div>}
      {list.map((s) => (
        <div key={s.id}>
          <div className="item" style={{ cursor: "default" }}>
            <span className="name">{s.folder_name ?? `folder #${s.source_folder_id}`}</span>
            {s.state !== "live" && <span className="badge off">{s.state}</span>}
            <ActBtn label={ICON.remove} cls="danger" title={s.state === "live" ? "Revoke this token" : "Remove this ended token"}
              onClick={() => confirmThen(s.state === "live" ? {
                title: `Revoke the token for "${s.folder_name}"?`,
                message: "Everyone who imported it loses access on their next question. A revoked token can't be brought back — share again for a new one.",
                confirmLabel: "Revoke", danger: true,
              } : {
                title: `Remove the ${s.state} token for "${s.folder_name}"?`,
                message: "It already grants nothing; this only clears it from the list.",
                confirmLabel: "Remove",
              }, () => unshareFolder(s.token))} />
          </div>
          <TokenLine s={s} />
        </div>
      ))}
    </Section>
  );
}

/** The footer's way into the account log, with how much of it is new. */
function ActivityButton() {
  const events = useEvents();
  const [open, setOpen] = useState(false);
  const unread = (events.data ?? []).filter((e) => !e.read).length;
  return (
    <>
      <button className={`sm${unread ? " primary" : ""}`} onClick={() => setOpen(true)}
        title={unread ? `${unread} new event(s) while you were away` : "What changed for your account and folders"}>
        Activity{unread ? ` · ${unread}` : ""}
      </button>
      {open && <EventsDialog onClose={() => setOpen(false)} />}
    </>
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
  // Hidden rather than unmounted: the scope effects below keep running.
  const hidden = useLayout((s) => s.sidebarHidden);
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
    <aside id="sidebar" style={{ width }} className={hidden ? "hide" : undefined}>
      <div className="side-scroll">
        <Sessions />
        <Folders nodes={tree.data} links={links.data} />
        <Imports nodes={tree.data} links={links.data} />
        <Shares />
        {/* Last: reports and their templates matter only when writing a report. */}
        <Section id="reports" title="Reports"><ReportList /></Section>
        <Templates />
      </div>
      <div className="side-foot">
        <span className="who-am-i" title={email}>{email}</span>
        <ActivityButton />
        {isAdmin && (
          <button className="sm" onClick={() => {
            history.replaceState(null, "", "#admin");
            setState({ adminOpen: true });
          }}>Admin</button>
        )}
        <button className="sm" onClick={() => guard(signOut)}>Sign out</button>
      </div>
      <Resizer />
    </aside>
  );
}
