/*
 * Who reads a folder besides its owner, one folder at a time: the user groups it
 * is granted to, and its share tokens. Every space-wide root folder is listed,
 * so granting one to a group starts here — pick the folder, pick the group.
 *
 * A folder can have several share tokens, and each token any number of
 * importers. Everyone who ever imported a token stays on its Importers list,
 * with whether they still read it; one import can be removed there. Revoking a
 * token removes every import of it.
 *
 * A token's expiry is set once, by whoever made it; the admin can't change it.
 * Revoked and expired are both final.
 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Dialog } from "../../ui/Dialog";
import { adminApi } from "../../api/client";
import type { AdminGroup, AdminRootFolder, AdminShare, AdminShareImport } from "../../api/types";
import { confirmThen } from "../../ui/confirm";
import { ICON } from "../../ui/icons";
import { guard, toast } from "../../ui/toast";
import { adminKeys, Badge, refreshAdmin, useAdminGroups, when } from "./common";
import { PickerRow } from "./GroupsTab";

interface FolderAccess {
  id: number;
  name: string;
  owner: string;
  documents: number | null;
  /** Null when the folder can't be granted (a subfolder, or session-only now). */
  groupIds: number[] | null;
  shares: AdminShare[];
  imports: AdminShareImport[];
}

const STATUS_CLS = { live: "admin", expired: "off", revoked: "dead" } as const;

/**
 * Every grantable root, plus any other folder a token was minted on, each with
 * its shares and everyone who imported them.
 */
function byFolder(roots: AdminRootFolder[], shares: AdminShare[], imports: AdminShareImport[]): FolderAccess[] {
  const folders = new Map<number, FolderAccess>();
  for (const r of roots) {
    folders.set(r.id, {
      id: r.id, name: r.name, owner: r.owner_email ?? "—", documents: r.document_count,
      groupIds: r.group_ids, shares: [], imports: [],
    });
  }
  for (const s of shares) {
    let f = folders.get(s.source_folder_id);
    if (!f) {
      f = {
        id: s.source_folder_id,
        name: s.source_folder_name ?? s.name ?? `folder #${s.source_folder_id}`,
        owner: s.created_by_email ?? "—",
        documents: null, groupIds: null, shares: [], imports: [],
      };
      folders.set(f.id, f);
    }
    f.shares.push(s);
  }
  const folderOf = new Map(shares.map((s) => [s.id, s.source_folder_id]));
  for (const i of imports) folders.get(folderOf.get(i.share_id) ?? -1)?.imports.push(i);
  return [...folders.values()].sort((a, b) => a.name.localeCompare(b.name));
}

/** The groups a folder is granted to, with revoke, and a picker to grant more. */
function FolderGroups({ folder, groups }: { folder: FolderAccess; groups: AdminGroup[] }) {
  if (folder.groupIds == null) {
    return <div className="empty">only a space-wide top-level folder can be granted to a group</div>;
  }
  const granted = new Set(folder.groupIds);
  const grantedGroups = groups.filter((g) => granted.has(g.id));
  return (
    <>
      {!grantedGroups.length && <div className="empty">not granted to any group</div>}
      {grantedGroups.map((g) => (
        <div key={g.id} className="arow">
          <div className="grow"><strong>{g.name}</strong></div>
          <span className="sub">{g.member_count} member(s)</span>
          <button className="sm danger" title={`Revoke from ${g.name}`} onClick={() =>
            confirmThen({
              title: `Revoke "${folder.name}" from ${g.name}?`,
              message: "Members lose it on their next question. The folder and its documents are untouched.",
              confirmLabel: "Revoke", danger: true,
            }, async () => {
              await adminApi("DELETE", `/groups/${g.id}/folders/${folder.id}`);
              toast(`Revoked from ${g.name}.`);
              await refreshAdmin();
            })}>{ICON.remove}</button>
        </div>
      ))}
      <PickerRow placeholder="Grant to a group…"
        empty={groups.length ? "granted to every group" : "no groups yet — create one on the User groups tab"}
        options={groups.filter((g) => !granted.has(g.id)).map((g) => ({ value: String(g.id), label: g.name }))}
        onPick={(groupId) => guard(async () => {
          const r = await adminApi<{ message?: string }>("POST", `/groups/${groupId}/folders`, { json: { folder_id: folder.id } });
          toast(r?.message ?? "Granted.");
          await refreshAdmin();
        })} />
    </>
  );
}

/** When the token ends, or how it ended — set by its maker, never edited here. */
function Expiry({ share }: { share: AdminShare }) {
  if (share.state === "revoked") return <div className="expiry sub">Revoked {when(share.revoked_at)} — it can't be brought back.</div>;
  if (share.state === "expired") return <div className="expiry sub">Expired {when(share.expires_at)} — it can't be brought back.</div>;
  return (
    <div className="expiry">
      <span className="sub">Expires:</span>
      <strong>{share.expires_at ? when(share.expires_at) : "never"}</strong>
    </div>
  );
}

const IMPORT_STATUS: Record<string, { label: string; cls: string }> = {
  using: { label: "using", cls: "admin" },
  removed: { label: "removed", cls: "off" },
  removed_by_admin: { label: "removed by admin", cls: "off" },
  revoked: { label: "token revoked", cls: "dead" },
  expired: { label: "token expired", cls: "dead" },
};
const importStatus = (s: string) => IMPORT_STATUS[s] ?? { label: s, cls: "off" };

/** Everyone who ever imported a token, using it or not. */
function ImportersDialog({ share, folder, imports, onClose }:
  { share: AdminShare; folder: FolderAccess; imports: AdminShareImport[]; onClose: () => void }) {
  return (
    <Dialog title={`Importers of token #${share.id}`} onClose={onClose}>
      <p className="sub">"{folder.name}", owned by {folder.owner}.</p>
      {!imports.length ? <div className="empty">nobody has imported it</div> : (
        <div className="tablewrap">
          <table className="utable static">
            <thead><tr><th>User</th><th>Status</th><th>First imported</th><th>Stopped</th><th /></tr></thead>
            <tbody>
              {imports.map((i) => {
                const st = importStatus(i.status);
                const who = i.user_email ?? "deleted user";
                return (
                  <tr key={i.id}>
                    <td className="who">{who}</td>
                    <td><Badge text={st.label} cls={st.cls} /></td>
                    <td className="sub">{when(i.first_imported_at)}</td>
                    <td className="sub">{i.stopped_at ? when(i.stopped_at) : "—"}</td>
                    <td>{i.link_id != null && (
                      <button className="sm danger" title="Remove this import" onClick={() =>
                        confirmThen({
                          title: `Remove "${folder.name}" from ${who}'s knowledge base?`,
                          message: "The owner's folder is untouched, and other importers keep it.",
                          confirmLabel: "Remove", danger: true,
                        }, async () => {
                          await adminApi("DELETE", `/folder-links/${i.link_id}`);
                          toast("Import removed.");
                          await refreshAdmin();
                        })}>{ICON.remove}</button>
                    )}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="actions"><button className="sm" onClick={onClose}>Close</button></div>
    </Dialog>
  );
}

function FolderDetail({ folder, groups }: { folder: FolderAccess; groups: AdminGroup[] }) {
  const [openShare, setOpenShare] = useState<number | null>(null);
  return (
    <aside className="udetail wide">
      <header>
        <div className="grow"><strong>{folder.name}</strong><div className="sub">owned by {folder.owner}</div></div>
      </header>
      <h4>User groups</h4>
      <div className="sub">Every member reads it, subfolders included, read-only.</div>
      <FolderGroups folder={folder} groups={groups} />
      <h4>Share tokens</h4>
      {!folder.shares.length && <div className="empty">none — the owner makes one with Share in their sidebar</div>}
      {folder.shares.map((s) => {
        const imports = folder.imports.filter((i) => i.share_id === s.id);
        const using = imports.filter((i) => i.status === "using").length;
        return (
          <div key={s.id} className="share">
            <header>
              <span className="grow">Token #{s.id}<span className="sub"> · created {when(s.created_at)}</span></span>
              <Badge text={s.state} cls={STATUS_CLS[s.state]} />
              <button className="sm" title="Everyone who imported this token" onClick={() => setOpenShare(s.id)}>
                Importers{imports.length ? ` · ${using}/${imports.length}` : ""}
              </button>
              {s.state !== "revoked" && <button className="sm danger" title="Revoke: end the token and remove it from every importer"
                onClick={() =>
                confirmThen({
                  title: `Revoke token #${s.id} of "${folder.name}"?`,
                  message: `It is removed from all ${s.link_count} importer(s)' knowledge bases immediately, and can't be brought back.`,
                  confirmLabel: "Revoke", danger: true,
                }, async () => {
                  const r = await adminApi<{ links_removed?: number }>("DELETE", `/folder-shares/${s.id}`);
                  toast(`Revoked — removed from ${r?.links_removed ?? 0} knowledge base(s).`);
                  await refreshAdmin();
                })}>{ICON.remove}</button>}
            </header>
            <Expiry share={s} />
            {openShare === s.id && <ImportersDialog share={s} folder={folder} imports={imports} onClose={() => setOpenShare(null)} />}
          </div>
        );
      })}
    </aside>
  );
}

export function SharesTab() {
  const roots = useQuery({ queryKey: adminKeys.folders, queryFn: () => adminApi<AdminRootFolder[]>("GET", "/folders") });
  const shares = useQuery({ queryKey: adminKeys.shares, queryFn: () => adminApi<AdminShare[]>("GET", "/folder-shares") });
  const imports = useQuery({ queryKey: adminKeys.imports, queryFn: () => adminApi<AdminShareImport[]>("GET", "/folder-share-imports") });
  const groups = useAdminGroups();
  const [selected, setSelected] = useState<number | null>(null);
  const [filter, setFilter] = useState("");
  const groupList = groups.data ?? [];
  const groupName = new Map(groupList.map((g) => [g.id, g.name]));
  const folders = byFolder(roots.data ?? [], shares.data ?? [], imports.data ?? []);
  const needle = filter.trim().toLowerCase();
  const shown = needle
    ? folders.filter((f) => f.name.toLowerCase().includes(needle) || f.owner.toLowerCase().includes(needle))
    : folders;
  const current = folders.find((f) => f.id === selected);
  const loading = roots.isPending || shares.isPending || imports.isPending;

  return (
    <section className="apane">
      <h3 style={{ marginTop: 0 }}>Folder access — pick a folder to grant it to user groups or manage its share tokens</h3>
      <div className="toolbar">
        <input className="grow" placeholder="Filter by folder or owner…" value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>
      <div className="asplit">
        <div className="tablewrap">
          <table className="utable">
            <thead><tr>
              <th>Folder</th><th>Owner</th><th className="num">Docs</th><th>Groups</th>
              <th className="num">Tokens</th><th className="num">Importers</th>
            </tr></thead>
            <tbody>
              {!shown.length && (
                <tr><td className="empty" colSpan={6}>{loading ? "loading…" : needle ? "no folder matches" : "no folders yet"}</td></tr>
              )}
              {shown.map((f) => {
                const live = f.shares.filter((s) => s.state === "live").length;
                return (
                  <tr key={f.id} className={f.id === selected ? "sel" : ""}
                    onClick={() => setSelected(f.id === selected ? null : f.id)}>
                    <td><strong>{f.name}</strong></td>
                    <td className="sub">{f.owner}</td>
                    <td className="num">{f.documents ?? "—"}</td>
                    <td>{f.groupIds?.length
                      ? f.groupIds.map((id) => groupName.get(id) ?? `#${id}`).join(", ")
                      : <span className="sub">—</span>}</td>
                    <td className="num">
                      {f.shares.length || <span className="sub">—</span>}
                      {f.shares.length > 0 && !live && <> <Badge text="none live" cls="off" /></>}
                    </td>
                    <td className="num" title="Importers reading it now">{f.imports.filter((i) => i.status === "using").length || <span className="sub">—</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {current && <FolderDetail folder={current} groups={groupList} />}
      </div>
    </section>
  );
}
