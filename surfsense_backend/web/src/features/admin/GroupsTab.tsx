/*
 * A group is a named set of users plus a set of folder grants. Membership by
 * itself grants nothing: the grants make folders readable, and only an admin can
 * make one. That is why two people in the same group still cannot see each
 * other's uploads — only what was granted to the group.
 */
import { useQuery } from "@tanstack/react-query";
import { useState, type MouseEvent as ReactMouseEvent } from "react";
import { adminApi } from "../../api/client";
import type { AdminFolder, AdminGroup, AdminGroupFolder, AdminGroupMember } from "../../api/types";
import { useStore } from "../../store";
import { guard, toast } from "../../ui/toast";
import { adminKeys, Badge, refreshAdmin, useAdminUsers } from "./common";

/** A one-shot <select> that fires `onPick` and resets itself. */
function PickerRow({ placeholder, empty, options, onPick }:
  { placeholder: string; empty: string; options: { value: string; label: string }[]; onPick: (v: string) => void }) {
  return (
    <div className="arow">
      <select value="" onChange={(e) => { if (e.target.value) onPick(e.target.value); }}>
        <option value="">{options.length ? placeholder : empty}</option>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
}

/** Grant a folder: pick whose folders, then which. Includes this admin's own. */
function GrantPicker({ groupId, granted }: { groupId: number; granted: Set<number> }) {
  const me = useStore((s) => s.email);
  const users = useAdminUsers();
  const [owner, setOwner] = useState("");
  const folders = useQuery({
    queryKey: adminKeys.userFolders(owner),
    queryFn: () => adminApi<AdminFolder[]>("GET", `/users/${owner}/folders`),
    enabled: !!owner,
  });
  // A session-scoped folder is invisible even to its owner's other chats, so the
  // server refuses to grant one; don't offer it.
  const pickable = (folders.data ?? []).filter((f) => f.owner_thread_id == null && !granted.has(f.id));

  return (
    <div className="arow">
      <select value={owner} onChange={(e) => setOwner(e.target.value)}>
        <option value="">Grant a folder from…</option>
        {(users.data ?? []).map((u) => (
          <option key={u.id} value={u.id}>{u.email === me ? `${u.email} (you)` : u.email}</option>
        ))}
      </select>
      {owner && (
        <select value="" onChange={(e) => {
          const folderId = Number(e.target.value);
          if (!folderId) return;
          guard(async () => {
            const r = await adminApi<{ message?: string }>("POST", `/groups/${groupId}/folders`, { json: { folder_id: folderId } });
            toast(r?.message ?? "Granted.");
            await refreshAdmin();
          });
        }}>
          <option value="">{folders.isPending ? "loading…" : pickable.length ? "Pick a folder…" : "no grantable folders"}</option>
          {pickable.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
        </select>
      )}
    </div>
  );
}

/** The selected group: who is in it, and which folders it reads. */
function GroupDetail({ groupId, name }: { groupId: number; name: string }) {
  const users = useAdminUsers();
  const members = useQuery({
    queryKey: adminKeys.groupMembers(groupId),
    queryFn: () => adminApi<AdminGroupMember[]>("GET", `/groups/${groupId}/members`),
  });
  const folders = useQuery({
    queryKey: adminKeys.groupFolders(groupId),
    queryFn: () => adminApi<AdminGroupFolder[]>("GET", `/groups/${groupId}/folders`),
  });
  const memberIds = new Set((members.data ?? []).map((m) => m.user_id));

  return (
    <aside className="udetail" id="groupDetail">
      <header><div className="grow"><strong>{name}</strong></div></header>
      <h4>Members</h4>
      {(members.data ?? []).map((m) => (
        <div key={m.user_id} className="arow">
          <div className="grow">{m.display_name ? `${m.display_name} <${m.email}>` : m.email}</div>
          {m.is_superuser && <Badge text="admin" cls="admin" />}
          <button className="sm danger" onClick={() => guard(async () => {
            await adminApi("DELETE", `/groups/${groupId}/members/${m.user_id}`);
            await refreshAdmin();
          })}>Remove</button>
        </div>
      ))}
      <PickerRow placeholder="Add member…" empty="no one left to add"
        options={(users.data ?? []).filter((u) => !memberIds.has(u.id)).map((u) => ({ value: u.id, label: u.email }))}
        onPick={(userId) => guard(async () => {
          await adminApi("POST", `/groups/${groupId}/members`, { json: { user_id: userId } });
          await refreshAdmin();
        })} />

      <h4>Folders this group reads</h4>
      <div className="sub">
        Read-only for every member, subfolders included. Upload general documents
        into one of your own folders, then grant it here.
      </div>
      {(folders.data ?? []).map((f) => (
        <div key={f.folder_id} className="arow">
          <div className="grow">{f.name}</div>
          <span className="sub">{f.owner_email ?? "—"} · {f.document_count} doc(s)</span>
          <button className="sm danger" onClick={() => guard(async () => {
            await adminApi("DELETE", `/groups/${groupId}/folders/${f.folder_id}`);
            toast("Revoked — members lose it on their next question.");
            await refreshAdmin();
          })}>Revoke</button>
        </div>
      ))}
      <GrantPicker groupId={groupId} granted={new Set((folders.data ?? []).map((f) => f.folder_id))} />
    </aside>
  );
}

/** A group's row; Edit swaps its name and description for inputs, in place. */
function GroupRow({ g, selected, onSelect, onDeleted }:
  { g: AdminGroup; selected: boolean; onSelect: () => void; onDeleted: () => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(g.name);
  const [desc, setDesc] = useState(g.description ?? "");
  const stop = (e: ReactMouseEvent) => e.stopPropagation();

  const save = () => guard(async () => {
    if (!name.trim()) { toast("A group needs a name.", "warn"); return; }
    // "" clears the description; the server stores it as none.
    await adminApi("PATCH", `/groups/${g.id}`, { json: { name: name.trim(), description: desc.trim() } });
    setEditing(false);
    await refreshAdmin();
  });

  if (editing) {
    return (
      <tr className="sel editing">
        <td><input value={name} maxLength={100} autoFocus onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }} /></td>
        <td><input value={desc} maxLength={500} placeholder="What it is for"
          onChange={(e) => setDesc(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }} /></td>
        <td className="num">{g.member_count}</td>
        <td className="num">{g.folder_count}</td>
        <td className="acts">
          <button className="sm primary" onClick={save}>Save</button>
          <button className="sm" onClick={() => { setName(g.name); setDesc(g.description ?? ""); setEditing(false); }}>Cancel</button>
        </td>
      </tr>
    );
  }
  return (
    <tr className={selected ? "sel" : ""} onClick={onSelect}>
      <td><strong>{g.name}</strong></td>
      <td className={g.description ? "" : "sub"}>{g.description || "—"}</td>
      <td className="num">{g.member_count}</td>
      <td className="num">{g.folder_count}</td>
      <td className="acts" onClick={stop}>
        <button className="sm" onClick={() => { setName(g.name); setDesc(g.description ?? ""); setEditing(true); }}>Edit</button>
        <button className="sm danger" onClick={() => {
          if (confirm(`Delete the group "${g.name}"?

Its ${g.member_count} member(s) lose access to its ${g.folder_count} granted folder(s) at once. No documents are deleted.`)) {
            guard(async () => {
              await adminApi("DELETE", `/groups/${g.id}`);
              onDeleted();
              toast("Group deleted.");
              await refreshAdmin();
            });
          }
        }}>Delete</button>
      </td>
    </tr>
  );
}

export function GroupsTab() {
  const groups = useQuery({ queryKey: adminKeys.groups, queryFn: () => adminApi<AdminGroup[]>("GET", "/groups") });
  const [selected, setSelected] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const list = groups.data ?? [];
  const current = list.find((g) => g.id === selected);

  return (
    <section className="apane">
      <h3 style={{ marginTop: 0 }}>User groups — everyone in a group reads the folders granted to it</h3>
      <form className="toolbar" autoComplete="off" onSubmit={(e) => {
        e.preventDefault();
        guard(async () => {
          const made = await adminApi<AdminGroup>("POST", "/groups", {
            json: { name: name.trim(), description: desc.trim() || undefined },
          });
          setName(""); setDesc("");
          setSelected(made.id);
          toast(`Created ${made.name}. Add members, then grant it the folders they should read.`, "ok", 9000);
          await refreshAdmin();
        });
      }}>
        <input placeholder="Group name, e.g. Analysts" maxLength={100} required value={name} onChange={(e) => setName(e.target.value)} />
        <input className="grow" placeholder="What it is for (optional)" maxLength={500} value={desc} onChange={(e) => setDesc(e.target.value)} />
        <button type="submit" className="primary sm">+ New group</button>
      </form>
      <div className="asplit">
        <div className="tablewrap">
          <table className="utable gtable">
            <thead><tr>
              <th>Group</th><th>Description</th><th className="num">Members</th><th className="num">Folders</th><th />
            </tr></thead>
            <tbody>
              {!list.length && (
                <tr><td className="empty" colSpan={5}>{groups.isPending ? "loading…" : "no groups yet"}</td></tr>
              )}
              {list.map((g) => (
                <GroupRow key={g.id} g={g} selected={g.id === selected}
                  onSelect={() => setSelected(selected === g.id ? null : g.id)}
                  onDeleted={() => setSelected(null)} />
              ))}
            </tbody>
          </table>
        </div>
        {current && <GroupDetail key={current.id} groupId={current.id} name={current.name} />}
      </div>
    </section>
  );
}
