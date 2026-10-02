import { useQueries, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { adminApi } from "../../api/client";
import type { AdminFolder, AdminGroupMember, AdminUser } from "../../api/types";
import { useStore } from "../../store";
import { confirmThen } from "../../ui/confirm";
import { ICON } from "../../ui/icons";
import { guard, toast } from "../../ui/toast";
import { byName } from "../sidebar/FolderTree";
import { adminKeys, Badge, refreshAdmin, useAdminGroups, useAdminUsers, when } from "./common";

type SortKey = "email" | "is_superuser" | "is_active" | "last_login" | "folder_count" | "document_count";

function sortValue(u: AdminUser, key: SortKey): string | number {
  if (key === "email") return u.email.toLowerCase();
  if (key === "last_login") return u.last_login ? Date.parse(u.last_login) : 0;
  const v = u[key];
  if (typeof v === "boolean") return v ? 1 : 0;
  return v ?? 0;
}

const COLUMNS: { key: SortKey; label: string; num?: boolean }[] = [
  { key: "email", label: "User" },
  { key: "is_superuser", label: "Role" },
  { key: "is_active", label: "Status" },
  { key: "last_login", label: "Last sign-in" },
  { key: "folder_count", label: "Folders", num: true },
  { key: "document_count", label: "Documents", num: true },
];

function NewUserForm({ onDone }: { onDone: (id?: string) => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [admin, setAdmin] = useState(false);
  return (
    <form className="card" autoComplete="off" onSubmit={(e) => {
      e.preventDefault();
      guard(async () => {
        const made = await adminApi<AdminUser>("POST", "/users", {
          json: { email: email.trim(), password, display_name: name.trim() || undefined, is_superuser: admin },
        });
        toast(`Created ${made.email}. They can sign in now with that password.`, "ok", 9000);
        await refreshAdmin();
        onDone(made.id);
      });
    }}>
      <h4>New user</h4>
      <div className="fgrid">
        <div><label>Email</label><input type="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} /></div>
        <div><label>Display name (optional)</label><input value={name} onChange={(e) => setName(e.target.value)} /></div>
        <div><label>Password (min. 8 characters)</label>
          <input type="password" minLength={8} required autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} /></div>
        <label className="check"><input type="checkbox" checked={admin} onChange={(e) => setAdmin(e.target.checked)} /> System admin</label>
      </div>
      <div className="actions">
        <button type="submit" className="primary sm">Create user</button>
        <button type="button" className="sm" onClick={() => onDone()}>Cancel</button>
      </div>
    </form>
  );
}

/** One user's folders, nested by parent_id, with the admin actions on each root. */
function UserFolders({ user }: { user: AdminUser }) {
  const q = useQuery({
    queryKey: adminKeys.userFolders(user.id),
    queryFn: () => adminApi<AdminFolder[]>("GET", `/users/${user.id}/folders`),
  });
  if (q.isPending) return <div className="empty">loading…</div>;
  if (q.isError) return <div className="empty">could not load: {q.error.message}</div>;
  const folders = q.data ?? [];
  if (!folders.length) return <div className="empty">no folders</div>;

  const ids = new Set(folders.map((f) => f.id));
  const childrenOf = new Map<number | null, AdminFolder[]>();
  for (const f of folders) {
    const pid = f.parent_id != null && ids.has(f.parent_id) ? f.parent_id : null;
    if (!childrenOf.has(pid)) childrenOf.set(pid, []);
    childrenOf.get(pid)!.push(f);
  }

  const rows: React.ReactNode[] = [];
  const walk = (pid: number | null, depth: number) => {
    for (const f of (childrenOf.get(pid) ?? []).sort(byName)) {
      rows.push(
        <div key={f.id} className="arow" style={{ paddingLeft: `${8 + depth * 14}px` }}>
          <div className="grow">
            <span>{f.name}</span>
            {depth === 0 && f.owner_thread_id != null && <Badge text={`session #${f.owner_thread_id}`} cls="off" />}
          </div>
          {depth === 0 && f.owner_thread_id != null && (
            <button className="sm" title="Make this folder space-wide: every session of theirs will see it." onClick={() =>
              confirmThen({
                title: `Make "${f.name}" space-wide?`,
                message: `All of ${user.email}'s sessions will see it — and so will every admin.`,
                confirmLabel: "Make space-wide",
              }, async () => {
                await adminApi("PATCH", `/folders/${f.id}/scope`, { json: { scope: "space" } });
                toast(`"${f.name}" is now space-wide.`);
                await refreshAdmin();
              })}>{ICON.promote}</button>
          )}
          {depth === 0 && (
            <button className="sm danger" title="Delete this folder and its documents" onClick={() =>
              confirmThen({
                title: `Delete "${f.name}" of ${user.email}?`,
                message: "The folder and all of its documents are deleted, and with them every share and group grant on it. This can't be undone.",
                confirmLabel: "Delete", danger: true,
              }, async () => {
                await adminApi("DELETE", `/folders/${f.id}`);
                toast(`Deleting "${f.name}" (documents are queued for removal).`);
                await refreshAdmin();
              })}>{ICON.remove}</button>
          )}
        </div>,
      );
      walk(f.id, depth + 1);
    }
  };
  walk(null, 0);
  return <div className="afolders">{rows}</div>;
}

/**
 * The groups this user is in, with add/remove. There is no per-user endpoint,
 * so it reads every group's member list — the same queries the Groups tab
 * caches, and an install has a handful of groups.
 */
function UserGroups({ user }: { user: AdminUser }) {
  const groups = useAdminGroups();
  const list = groups.data ?? [];
  const members = useQueries({
    queries: list.map((g) => ({
      queryKey: adminKeys.groupMembers(g.id),
      queryFn: () => adminApi<AdminGroupMember[]>("GET", `/groups/${g.id}/members`),
    })),
  });
  if (groups.isPending || members.some((m) => m.isPending)) return <div className="empty">loading…</div>;
  if (!list.length) return <div className="empty">no groups yet — create one on the Groups tab</div>;
  const inGroup = list.filter((_, i) => members[i].data?.some((m) => m.user_id === user.id));
  const others = list.filter((g) => !inGroup.includes(g));

  if (user.is_superuser && !inGroup.length) {
    return <div className="empty">admins aren't added to groups — they already read every user's folders</div>;
  }
  return (
    <>
      {!inGroup.length && <div className="empty">not in any group</div>}
      {inGroup.map((g) => (
        <div key={g.id} className="arow">
          <div className="grow">{g.name}</div>
          <span className="sub">{g.folder_count} folder(s)</span>
          <button className="sm danger" title={`Remove from ${g.name}`} onClick={() =>
            confirmThen({
              title: `Remove ${user.email} from ${g.name}?`,
              message: `They lose the group's ${g.folder_count} folder(s) on their next question.`,
              confirmLabel: "Remove", danger: true,
            }, async () => {
              await adminApi("DELETE", `/groups/${g.id}/members/${user.id}`);
              toast(`Removed ${user.email} from ${g.name}.`);
              await refreshAdmin();
            })}>{ICON.remove}</button>
        </div>
      ))}
      {!user.is_superuser && <div className="arow">
        <select value="" disabled={!others.length} onChange={(e) => {
          const g = others.find((x) => x.id === Number(e.target.value));
          if (!g) return;
          guard(async () => {
            const r = await adminApi<{ message?: string }>("POST", `/groups/${g.id}/members`, { json: { user_id: user.id } });
            toast(r?.message ?? `Added ${user.email} to ${g.name}.`);
            await refreshAdmin();
          });
        }}>
          <option value="">{others.length ? "Add to group…" : "already in every group"}</option>
          {others.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
        </select>
      </div>}
    </>
  );
}

function UserDetail({ u, onClose }: { u: AdminUser; onClose: () => void }) {
  const me = useStore((s) => s.email);
  // The server refuses self-demotion, self-deactivation and self-deletion; the
  // page greys those out instead of offering buttons that can only 400.
  const self = u.email === me;
  const [name, setName] = useState(u.display_name ?? "");
  const [pw, setPw] = useState("");
  useEffect(() => setName(u.display_name ?? ""), [u.id, u.display_name]);
  const selfTitle = self ? "You can't change your own access." : undefined;

  return (
    <aside className="udetail">
      <header>
        <div className="grow"><strong>{u.display_name || u.email}</strong><div className="sub">{u.email}</div></div>
        <button className="sm" title="Close" onClick={onClose}>{ICON.remove}</button>
      </header>
      <div className="sub" style={{ marginTop: 8, whiteSpace: "pre-line" }}>
        {`${u.search_space_count} space(s) · ${u.folder_count} folder(s) · ${u.document_count} document(s)\nlast sign-in: ${when(u.last_login)}`}
      </div>

      <h3>Display name</h3>
      <div className="stack">
        <input placeholder="shown instead of the email" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="sm" disabled={name.trim() === (u.display_name ?? "")} onClick={() =>
          confirmThen({
            title: `Change ${u.email}'s display name?`,
            message: name.trim() ? `It becomes "${name.trim()}".` : "It is cleared; their email shows instead.",
            confirmLabel: "Save",
          }, async () => {
            await adminApi("PATCH", `/users/${u.id}`, { json: { display_name: name } });
            toast("Name saved.");
            await refreshAdmin();
          })}>Save</button>
      </div>

      <h3>Access</h3>
      <div className="actions" style={{ marginTop: 0 }}>
        <button className="sm" disabled={self} title={selfTitle} onClick={() =>
          confirmThen(u.is_superuser ? {
            title: `Remove admin from ${u.email}?`,
            message: "They lose this page, and other users' folders disappear from their folder tree and answers.",
            confirmLabel: "Remove admin", danger: true,
          } : {
            title: `Make ${u.email} a system admin?`,
            message: "They get this page, and every non-admin user's folders appear — read-only — in their folder tree and answers. They are removed from every user group.",
            confirmLabel: "Make admin",
          }, async () => {
            await adminApi("PATCH", `/users/${u.id}`, { json: { is_superuser: !u.is_superuser } });
            toast(u.is_superuser ? `${u.email} is no longer an admin.` : `${u.email} is now an admin.`);
            await refreshAdmin();
          })}>{u.is_superuser ? "Remove admin" : "Make admin"}</button>
        <button className="sm" disabled={self} title={selfTitle} onClick={() =>
          confirmThen(u.is_active ? {
            title: `Deactivate ${u.email}?`,
            message: "They are signed out on their next request and can't sign in until reactivated. Nothing of theirs is deleted.",
            confirmLabel: "Deactivate", danger: true,
          } : {
            title: `Activate ${u.email}?`,
            message: "They can sign in again.",
            confirmLabel: "Activate",
          }, async () => {
            await adminApi("PATCH", `/users/${u.id}`, { json: { is_active: !u.is_active } });
            toast(u.is_active ? `Deactivated ${u.email}.` : `Activated ${u.email}.`);
            await refreshAdmin();
          })}>{u.is_active ? "Deactivate" : "Activate"}</button>
      </div>
      <div className="note">
        {u.is_superuser
          ? "Admins manage users and read every non-admin user's folders."
          : "Their space-wide folders are readable by every admin; nothing of theirs can be changed from an admin's chat."}
      </div>

      <h3>Reset password</h3>
      <div className="stack">
        <input type="password" placeholder="new password (min. 8)" autoComplete="new-password"
          value={pw} onChange={(e) => setPw(e.target.value)} />
        <button className="sm" onClick={() => {
          if (pw.length < 8) { toast("Passwords need at least 8 characters.", "warn"); return; }
          void confirmThen({
            title: `Reset ${u.email}'s password?`,
            message: "Their old password stops working. Hand the new password to them yourself.",
            confirmLabel: "Reset password", danger: true,
          }, async () => {
            await adminApi("POST", `/users/${u.id}/password`, { json: { password: pw } });
            setPw("");
            toast(`Password updated for ${u.email}. Browsers already signed in stay signed in ` +
              "until their session expires — deactivate the account to cut them off now.", "ok", 12000);
          });
        }}>Set</button>
      </div>

      <h3>Groups</h3>
      <UserGroups user={u} />

      <h3>Folders</h3>
      <UserFolders user={u} />

      <h3>Danger zone</h3>
      <div className="dangerzone">
        <div className="sub">Deletes the account and everything it owns: spaces, folders, documents and chats.</div>
        <button className="sm danger" style={{ marginTop: 8 }} disabled={self}
          title={self ? "You can't delete your own account." : undefined} onClick={() =>
            confirmThen({
              title: `Delete user ${u.email}?`,
              message: "ALL their data goes with them: spaces, folders, documents and chats. This can't be undone.",
              confirmLabel: "Delete user", danger: true,
            }, async () => {
              await adminApi("DELETE", `/users/${u.id}`);
              toast(`Deleted ${u.email}.`);
              onClose();
              await refreshAdmin();
            })}>Delete user</button>
      </div>
    </aside>
  );
}

export function UsersTab({ selected, setSelected }:
  { selected: string | null; setSelected: (id: string | null) => void }) {
  const me = useStore((s) => s.email);
  const users = useAdminUsers();
  const [search, setSearch] = useState("");
  const [role, setRole] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: number }>({ key: "email", dir: 1 });
  const [creating, setCreating] = useState(false);

  const all = users.data ?? [];
  const q = search.trim().toLowerCase();
  const shown = all
    .filter((u) => !q || u.email.toLowerCase().includes(q) || (u.display_name ?? "").toLowerCase().includes(q))
    .filter((u) => !role || (role === "admin") === u.is_superuser)
    .filter((u) => !status || (status === "active") === u.is_active)
    .sort((a, b) => {
      const x = sortValue(a, sort.key), y = sortValue(b, sort.key);
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
    });
  const admins = all.filter((u) => u.is_superuser).length;
  const current = all.find((u) => u.id === selected);

  return (
    <section className="apane">
      <div className="toolbar">
        <input placeholder="Search by email or name…" value={search} onChange={(e) => setSearch(e.target.value)} />
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="">All roles</option>
          <option value="admin">Admins</option>
          <option value="user">Users</option>
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any status</option>
          <option value="active">Active</option>
          <option value="inactive">Deactivated</option>
        </select>
        <span className="sub grow">{shown.length} of {all.length} users · {admins} admin{admins === 1 ? "" : "s"}</span>
        <button className="primary sm" onClick={() => setCreating(!creating)}>+ New user</button>
      </div>
      {creating && <NewUserForm onDone={(id) => { setCreating(false); if (id) setSelected(id); }} />}
      <div className="asplit">
        <div className="tablewrap">
          <table className="utable">
            <thead><tr>
              {COLUMNS.map((c) => (
                <th key={c.key} className={c.num ? "num" : undefined}
                  data-dir={sort.key === c.key ? (sort.dir > 0 ? "▲" : "▼") : undefined}
                  onClick={() => setSort({ key: c.key, dir: sort.key === c.key ? -sort.dir : 1 })}>{c.label}</th>
              ))}
            </tr></thead>
            <tbody>
              {!shown.length && (
                <tr><td className="empty" colSpan={6}>
                  {users.isPending ? "loading…" : all.length ? "no users match" : "no users"}
                </td></tr>
              )}
              {shown.map((u) => (
                <tr key={u.id} className={u.id === selected ? "sel" : ""}
                  onClick={() => setSelected(u.id === selected ? null : u.id)}>
                  <td>
                    <div>{u.display_name || u.email}{u.email === me && <Badge text="you" />}</div>
                    {u.display_name && <div className="sub">{u.email}</div>}
                  </td>
                  <td><Badge text={u.is_superuser ? "admin" : "user"} cls={u.is_superuser ? "admin" : ""} /></td>
                  <td><Badge text={u.is_active ? "active" : "deactivated"} cls={u.is_active ? "" : "off"} /></td>
                  <td className="sub">{when(u.last_login)}</td>
                  <td className="num">{u.folder_count}</td>
                  <td className="num">{u.document_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {current && <UserDetail key={current.id} u={current} onClose={() => setSelected(null)} />}
      </div>
    </section>
  );
}
