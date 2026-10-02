/*
 * Deployment-wide settings. Today: how long each kind of folder is kept before
 * the daily retention run deletes it with its documents. The deployment's env
 * sets defaults; saving here replaces them until "Use deployment defaults".
 */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { adminApi } from "../../api/client";
import type { RetentionDays, RetentionSettings } from "../../api/types";
import { confirmThen } from "../../ui/confirm";
import { toast } from "../../ui/toast";
import { refreshAdmin } from "./common";

const KINDS: { key: keyof RetentionDays; label: string; help: string }[] = [
  { key: "session", label: "Session-only folders", help: "uploaded for one chat session" },
  { key: "group", label: "Group folders", help: "granted to at least one user group" },
  { key: "admin", label: "Admin folders", help: "space-wide, uploaded by a system admin" },
  { key: "space", label: "User folders", help: "every other space-wide folder, promoted ones included" },
];

const days = (n: number | null) => (n ? `${n} day${n === 1 ? "" : "s"}` : "forever");

type Draft = Record<keyof RetentionDays, string>;
const toDraft = (d: RetentionDays): Draft =>
  Object.fromEntries(KINDS.map(({ key }) => [key, d[key] ? String(d[key]) : ""])) as Draft;

export function SettingsTab() {
  const q = useQuery({
    queryKey: ["admin", "settings", "retention"],
    queryFn: () => adminApi<RetentionSettings>("GET", "/settings/retention"),
  });
  const [draft, setDraft] = useState<Draft | null>(null);
  useEffect(() => { if (q.data) setDraft(toDraft(q.data.effective)); }, [q.data]);

  if (q.isPending || !draft) return <section className="apane"><div className="empty">loading…</div></section>;
  if (q.isError) return <section className="apane"><div className="empty">could not load: {q.error.message}</div></section>;
  const s = q.data;

  const parsed = Object.fromEntries(KINDS.map(({ key }) => {
    const v = draft[key].trim();
    return [key, v === "" ? null : Number(v)];
  })) as unknown as RetentionDays;
  const valid = KINDS.every(({ key }) => parsed[key] == null || (Number.isInteger(parsed[key]) && parsed[key]! >= 0));
  const changed = KINDS.some(({ key }) => (parsed[key] || null) !== (s.effective[key] || null));
  const shorter = KINDS.filter(({ key }) => parsed[key] && (!s.effective[key] || parsed[key]! < s.effective[key]!));

  const save = () => confirmThen({
    title: "Save folder retention?",
    message: KINDS.map(({ key, label }) => `${label}: ${days(parsed[key])}`).join("\n") +
      (shorter.length
        ? "\n\nFolders already older than a shorter period are deleted within a day, documents included."
        : ""),
    confirmLabel: "Save", danger: shorter.length > 0,
  }, async () => {
    await adminApi("PUT", "/settings/retention", { json: parsed });
    toast("Retention saved.");
    await refreshAdmin();
  });

  const reset = () => confirmThen({
    title: "Use the deployment defaults?",
    message: KINDS.map(({ key, label }) => `${label}: ${days(s.deployment[key])}`).join("\n"),
    confirmLabel: "Use defaults",
  }, async () => {
    await adminApi("DELETE", "/settings/retention");
    toast("Retention follows the deployment defaults again.");
    await refreshAdmin();
  });

  return (
    <section className="apane">
      <h3 style={{ marginTop: 0 }}>Folder retention</h3>
      <div className="sub" style={{ marginBottom: 10 }}>
        Folders are deleted, with their documents, this many days after upload. Leave empty to
        keep them forever.
      </div>
      <div className="card retention">
        {KINDS.map(({ key, label, help }) => (
          <div key={key} className="arow">
            <div className="grow"><strong>{label}</strong><div className="sub">{help}</div></div>
            <input type="number" min={0} step={1} placeholder="forever" value={draft[key]}
              onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} />
            <span className="sub">days</span>
            <span className="sub default">default: {days(s.deployment[key])}</span>
          </div>
        ))}
        <div className="actions">
          <button className="primary sm" disabled={!valid || !changed} onClick={save}>Save</button>
          <button className="sm" disabled={!changed} onClick={() => setDraft(toDraft(s.effective))}>Discard</button>
          <span className="grow" />
          <span className="sub">{s.overridden ? "Set on this page." : "Following the deployment defaults."}</span>
          {s.overridden && <button className="sm" onClick={reset}>Use deployment defaults</button>}
        </div>
      </div>
    </section>
  );
}
