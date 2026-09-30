/*
 * Folder sharing, one folder at a time. A folder can have several share tokens,
 * and each token any number of importers; every import is shown as source →
 * target, so the admin can see exactly who reads whose folder and cut one pair
 * (remove the link), one token (hard-revoke) or change how long a token lasts.
 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { adminApi } from "../../api/client";
import type { AdminLink, AdminShare } from "../../api/types";
import { guard, toast } from "../../ui/toast";
import { adminKeys, Badge, refreshAdmin, when } from "./common";

interface SharedFolder {
  id: number;
  name: string;
  owner: string;
  shares: AdminShare[];
  links: AdminLink[];
}

const isExpired = (s: AdminShare) => !!s.expires_at && Date.parse(s.expires_at) <= Date.now();

function status(s: AdminShare): { text: string; cls: string } {
  if (s.revoked_at) return { text: "revoked", cls: "dead" };
  if (isExpired(s)) return { text: "expired", cls: "off" };
  return { text: "live", cls: "admin" };
}

/** Group every share and link under the folder it exposes. */
function byFolder(shares: AdminShare[], links: AdminLink[]): SharedFolder[] {
  const folders = new Map<number, SharedFolder>();
  for (const s of shares) {
    let f = folders.get(s.source_folder_id);
    if (!f) {
      f = {
        id: s.source_folder_id,
        name: s.source_folder_name ?? s.name ?? `folder #${s.source_folder_id}`,
        owner: s.created_by_email ?? "—",
        shares: [], links: [],
      };
      folders.set(f.id, f);
    }
    f.shares.push(s);
  }
  for (const l of links) folders.get(l.source_folder_id)?.links.push(l);
  return [...folders.values()].sort((a, b) => a.name.localeCompare(b.name));
}

/** "2026-10-07T09:30" in local time, what <input type="datetime-local"> speaks. */
const toLocalInput = (iso: string | null) => {
  if (!iso) return "";
  const d = new Date(iso);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
};

function Expiry({ share }: { share: AdminShare }) {
  const [value, setValue] = useState(toLocalInput(share.expires_at));
  const save = (expiresAt: string | null) => guard(async () => {
    await adminApi("PATCH", `/folder-shares/${share.id}`, { json: { expires_at: expiresAt } });
    toast(expiresAt ? `Token now expires ${when(expiresAt)}.` : "Token no longer expires.");
    await refreshAdmin();
  });
  if (share.revoked_at) return <div className="expiry sub">Revoked {when(share.revoked_at)} — it can't be brought back.</div>;
  return (
    <div className="expiry">
      <span className="sub">Expires:</span>
      <strong>{share.expires_at ? when(share.expires_at) : "never"}</strong>
      <input type="datetime-local" value={value} onChange={(e) => setValue(e.target.value)} />
      <button className="sm" disabled={!value} onClick={() => save(new Date(value).toISOString())}>Set</button>
      {share.expires_at && <button className="sm" onClick={() => save(null)}>Never expire</button>}
      {!isExpired(share) && (
        <button className="sm" title="Stop it answering now; importers keep their link and can be revived by a later date."
          onClick={() => save(new Date().toISOString())}>Expire now</button>
      )}
    </div>
  );
}

function FolderDetail({ folder }: { folder: SharedFolder }) {
  return (
    <aside className="udetail wide">
      <header>
        <div className="grow"><strong>{folder.name}</strong><div className="sub">owned by {folder.owner}</div></div>
      </header>
      {folder.shares.map((s) => {
        const st = status(s);
        const links = folder.links.filter((l) => l.share_id === s.id);
        return (
          <div key={s.id} className="share">
            <header>
              <span className="grow">Token #{s.id}<span className="sub"> · created {when(s.created_at)}</span></span>
              <Badge text={st.text} cls={st.cls} />
              <button className="sm danger" onClick={() => {
                if (confirm(`Hard-revoke token #${s.id} of "${folder.name}"?\n\nIt is removed from all ${s.link_count} importer(s)' knowledge bases immediately.`)) {
                  guard(async () => {
                    const r = await adminApi<{ links_removed?: number }>("DELETE", `/folder-shares/${s.id}`);
                    toast(`Revoked — removed from ${r?.links_removed ?? 0} knowledge base(s).`);
                    await refreshAdmin();
                  });
                }
              }}>Hard-revoke</button>
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
                <button className="sm danger" onClick={() => {
                  if (confirm(`Remove "${folder.name}" from ${l.target_owner_email ?? "that user"}'s knowledge base?\n\nThe owner's folder is untouched, and other importers keep it.`)) {
                    guard(async () => {
                      await adminApi("DELETE", `/folder-links/${l.id}`);
                      toast("Import removed.");
                      await refreshAdmin();
                    });
                  }
                }}>Remove</button>
              </div>
            ))}
          </div>
        );
      })}
    </aside>
  );
}

export function SharesTab() {
  const shares = useQuery({ queryKey: adminKeys.shares, queryFn: () => adminApi<AdminShare[]>("GET", "/folder-shares") });
  const links = useQuery({ queryKey: adminKeys.links, queryFn: () => adminApi<AdminLink[]>("GET", "/folder-links") });
  const [selected, setSelected] = useState<number | null>(null);
  const folders = byFolder(shares.data ?? [], links.data ?? []);
  const current = folders.find((f) => f.id === selected);

  return (
    <section className="apane">
      <h3 style={{ marginTop: 0 }}>Shared folders — pick one to see who imports it</h3>
      <div className="asplit">
        <div className="tablewrap">
          <table className="utable">
            <thead><tr>
              <th>Folder</th><th>Owner</th><th className="num">Tokens</th><th className="num">Importers</th><th>Status</th>
            </tr></thead>
            <tbody>
              {!folders.length && (
                <tr><td className="empty" colSpan={5}>{shares.isPending || links.isPending ? "loading…" : "no shared folders"}</td></tr>
              )}
              {folders.map((f) => {
                const live = f.shares.filter((s) => !s.revoked_at && !isExpired(s)).length;
                return (
                  <tr key={f.id} className={f.id === selected ? "sel" : ""}
                    onClick={() => setSelected(f.id === selected ? null : f.id)}>
                    <td><strong>{f.name}</strong></td>
                    <td className="sub">{f.owner}</td>
                    <td className="num">{f.shares.length}</td>
                    <td className="num">{f.links.length}</td>
                    <td>{live
                      ? <Badge text={`${live} live`} cls="admin" />
                      : <Badge text="none live" cls="off" />}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {current && <FolderDetail folder={current} />}
      </div>
    </section>
  );
}
