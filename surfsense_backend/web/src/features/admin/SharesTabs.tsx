/*
 * Who reads a folder besides its owner, one folder at a time: the user groups it
 * is granted to, and its share tokens. Every space-wide root folder is listed,
 * so granting one to a group starts here — pick the folder, pick the group.
 *
 * A folder can have several share tokens, and each token any number of
 * importers; every import is shown as source → target, so the admin can see
 * exactly who reads whose folder and cut one pair (remove the link), one token
 * (hard-revoke) or change how long a token lasts.
 *
 * A token is live until revoked or expired, and both are final: the expiry of a
 * live token can be moved, never cleared, never set in the past and never on an
 * ended token. Ending one now is a revoke.
 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { adminApi } from "../../api/client";
import type { AdminGroup, AdminLink, AdminRootFolder, AdminShare } from "../../api/types";
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
  links: AdminLink[];
}

const STATUS_CLS = { live: "admin", expired: "off", revoked: "dead" } as const;

/**
 * Every grantable root, plus any other folder a token was minted on, each with
 * the shares and links that expose it.
 */
function byFolder(roots: AdminRootFolder[], shares: AdminShare[], links: AdminLink[]): FolderAccess[] {
  const folders = new Map<number, FolderAccess>();
  for (const r of roots) {
    folders.set(r.id, {
      id: r.id, name: r.name, owner: r.owner_email ?? "—", documents: r.document_count,
      groupIds: r.group_ids, shares: [], links: [],
    });
  }
  for (const s of shares) {
    let f = folders.get(s.source_folder_id);
    if (!f) {
      f = {
        id: s.source_folder_id,
        name: s.source_folder_name ?? s.name ?? `folder #${s.source_folder_id}`,
        owner: s.created_by_email ?? "—",
        documents: null, groupIds: null, shares: [], links: [],
      };
      folders.set(f.id, f);
    }
    f.shares.push(s);
  }
  for (const l of links) folders.get(l.source_folder_id)?.links.push(l);
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

/** "2026-10-07T09:30" in local time, what <input type="datetime-local"> speaks. */
const toLocalInput = (iso: string | null) => {
  if (!iso) return "";
  const d = new Date(iso);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
};

function Expiry({ share }: { share: AdminShare }) {
  const [value, setValue] = useState(toLocalInput(share.expires_at));
  const save = (expiresAt: string) => confirmThen({
    title: `Expire token #${share.id} on ${when(expiresAt)}?`,
    message: "Importers keep reading it until then.",
    confirmLabel: "Save",
  }, async () => {
    await adminApi("PATCH", `/folder-shares/${share.id}`, { json: { expires_at: expiresAt } });
    toast(`Token now expires ${when(expiresAt)}.`);
    await refreshAdmin();
  });
  // Ended is final: say how, and offer nothing that would pretend otherwise.
  if (share.state === "revoked") return <div className="expiry sub">Revoked {when(share.revoked_at)} — it can't be brought back.</div>;
  if (share.state === "expired") return <div className="expiry sub">Expired {when(share.expires_at)} — it can't be brought back.</div>;
  const future = !!value && Date.parse(value) > Date.now();
  return (
    <div className="expiry">
      <span className="sub">Expires:</span>
      <strong>{share.expires_at ? when(share.expires_at) : "never"}</strong>
      <input type="datetime-local" value={value} min={toLocalInput(new Date().toISOString())}
        onChange={(e) => setValue(e.target.value)} />
      <button className="sm" disabled={!future} title={value && !future ? "Pick a time in the future — to end it now, revoke it." : undefined}
        onClick={() => save(new Date(value).toISOString())}>Set</button>
    </div>
  );
}

function FolderDetail({ folder, groups }: { folder: FolderAccess; groups: AdminGroup[] }) {
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
        const links = folder.links.filter((l) => l.share_id === s.id);
        return (
          <div key={s.id} className="share">
            <header>
              <span className="grow">Token #{s.id}<span className="sub"> · created {when(s.created_at)}</span></span>
              <Badge text={s.state} cls={STATUS_CLS[s.state]} />
              <button className="sm danger" title="Hard-revoke: end the token and remove it from every importer"
                disabled={s.state === "revoked" && !s.link_count} onClick={() =>
                confirmThen({
                  title: `Hard-revoke token #${s.id} of "${folder.name}"?`,
                  message: `It is removed from all ${s.link_count} importer(s)' knowledge bases immediately, and can't be brought back.`,
                  confirmLabel: "Hard-revoke", danger: true,
                }, async () => {
                  const r = await adminApi<{ links_removed?: number }>("DELETE", `/folder-shares/${s.id}`);
                  toast(`Revoked — removed from ${r?.links_removed ?? 0} knowledge base(s).`);
                  await refreshAdmin();
                })}>{ICON.remove}</button>
            </header>
            <Expiry key={`${s.id}:${s.expires_at}`} share={s} />
            <h4>Imported by</h4>
            {!links.length && <div className="empty">nobody</div>}
            {links.map((l) => (
              <div key={l.id} className="arow">
                <div className="grow flow">
                  <span className="end">{folder.owner} / {folder.name}</span>
                  <span className="arrow">→</span>
                  <span className="end"><strong>{l.target_owner_email ?? `space #${l.target_search_space_id}`}</strong></span>
                </div>
                <button className="sm danger" title="Remove this import" onClick={() =>
                  confirmThen({
                    title: `Remove "${folder.name}" from ${l.target_owner_email ?? "that user"}'s knowledge base?`,
                    message: "The owner's folder is untouched, and other importers keep it.",
                    confirmLabel: "Remove", danger: true,
                  }, async () => {
                    await adminApi("DELETE", `/folder-links/${l.id}`);
                    toast("Import removed.");
                    await refreshAdmin();
                  })}>{ICON.remove}</button>
              </div>
            ))}
          </div>
        );
      })}
    </aside>
  );
}

export function SharesTab() {
  const roots = useQuery({ queryKey: adminKeys.folders, queryFn: () => adminApi<AdminRootFolder[]>("GET", "/folders") });
  const shares = useQuery({ queryKey: adminKeys.shares, queryFn: () => adminApi<AdminShare[]>("GET", "/folder-shares") });
  const links = useQuery({ queryKey: adminKeys.links, queryFn: () => adminApi<AdminLink[]>("GET", "/folder-links") });
  const groups = useAdminGroups();
  const [selected, setSelected] = useState<number | null>(null);
  const [filter, setFilter] = useState("");
  const groupList = groups.data ?? [];
  const groupName = new Map(groupList.map((g) => [g.id, g.name]));
  const folders = byFolder(roots.data ?? [], shares.data ?? [], links.data ?? []);
  const needle = filter.trim().toLowerCase();
  const shown = needle
    ? folders.filter((f) => f.name.toLowerCase().includes(needle) || f.owner.toLowerCase().includes(needle))
    : folders;
  const current = folders.find((f) => f.id === selected);
  const loading = roots.isPending || shares.isPending || links.isPending;

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
                    <td className="num">{f.links.length || <span className="sub">—</span>}</td>
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
