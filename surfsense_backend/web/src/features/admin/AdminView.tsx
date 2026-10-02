/*
 * Shown only to system admins (is_superuser). Every action is also enforced
 * server-side by require_admin, so the client gate is only about not showing dead
 * buttons. A page of its own rather than an overlay: managing users means
 * scanning a table and working on one user beside it. "#admin" in the URL keeps
 * the page open across a reload.
 */
import { useEffect, useState } from "react";
import { refreshFolders } from "../../queryClient";
import { setState } from "../../store";
import { guard } from "../../ui/toast";
import { refreshAdmin } from "./common";
import { GroupsTab } from "./GroupsTab";
import { SettingsTab } from "./SettingsTab";
import { SharesTab } from "./SharesTabs";
import { UsersTab } from "./UsersTab";

const TABS = [
  { id: "users", label: "Users" },
  { id: "groups", label: "User groups" },
  { id: "shares", label: "Folder shares" },
  { id: "settings", label: "Settings" },
] as const;
type Tab = (typeof TABS)[number]["id"];

export function closeAdmin() {
  setState({ adminOpen: false });
  history.replaceState(null, "", location.pathname + location.search);
  // A role change moves users into or out of this admin's own folder tree.
  guard(refreshFolders);
}

/** "#admin/groups" — the open tab rides in the URL, so a reload keeps it. */
const tabFromHash = (): Tab => {
  const t = location.hash.split("/")[1];
  return TABS.some((x) => x.id === t) ? (t as Tab) : "users";
};

export function AdminView() {
  const [tab, setTabState] = useState<Tab>(tabFromHash);
  const setTab = (t: Tab) => {
    setTabState(t);
    history.replaceState(null, "", t === "users" ? "#admin" : `#admin/${t}`);
  };
  const [selectedUser, setSelectedUser] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // Close the open user first; a second Escape leaves the page.
      if (selectedUser) setSelectedUser(null);
      else closeAdmin();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [selectedUser]);

  return (
    <div id="adminView">
      <header className="abar">
        <span className="t">System admin</span>
        <nav className="tabs">
          {TABS.map((t) => (
            <button key={t.id} className={t.id === tab ? "on" : ""} onClick={() => setTab(t.id)}>{t.label}</button>
          ))}
        </nav>
        <span className="grow" />
        <button className="sm" onClick={() => guard(refreshAdmin)}>Refresh</button>
        <button className="sm" onClick={closeAdmin}>← Back to chat</button>
      </header>
      <div className="acontent">
        {tab === "users" && <UsersTab selected={selectedUser} setSelected={setSelectedUser} />}
        {tab === "groups" && <GroupsTab />}
        {tab === "shares" && <SharesTab />}
        {tab === "settings" && <SettingsTab />}
      </div>
    </div>
  );
}
