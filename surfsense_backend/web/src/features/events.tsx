/*
 * The account log: what changed for this user while they were away — made an
 * admin, added to a group, a folder granted, revoked or deleted, a share
 * imported. The server writes these as `account_event` notifications at the
 * moment of the change; the page polls for them and marks them read once seen.
 */
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { api, apiJson } from "../api/client";
import type { AccountEvent } from "../api/types";
import { keys, queryClient } from "../queryClient";
import { Dialog } from "../ui/Dialog";

const POLL_MS = 60_000;

const listEvents = async () =>
  (await apiJson<{ items: AccountEvent[] }>("GET", "/api/v1/notifications?type=account_event&limit=50"))?.items ?? [];

export const useEvents = () =>
  useQuery({ queryKey: keys.events, queryFn: listEvents, refetchInterval: POLL_MS, refetchOnWindowFocus: true });

export function EventsDialog({ onClose }: { onClose: () => void }) {
  const events = useEvents();
  const list = events.data ?? [];
  const unread = list.some((e) => !e.read);

  // Opening the log is reading it. The unread rows stay highlighted until the
  // dialog closes, so what was new is still visible.
  useEffect(() => {
    if (!unread) return;
    void api("PATCH", "/api/v1/notifications/read-all").catch(() => {});
    return () => { void queryClient.invalidateQueries({ queryKey: keys.events }); };
  }, []);   // only on open: marking read must not re-run when the list refetches

  return (
    <Dialog title="Activity" onClose={onClose}>
      <p className="sub">Changes to your account and folders made by admins, other users, or retention.</p>
      <div className="events">
        {events.isPending && <div className="empty">loading…</div>}
        {events.data && !list.length && <div className="empty">nothing yet</div>}
        {list.map((e) => (
          <div key={e.id} className={`event${e.read ? "" : " unread"}`}>
            <div className="etitle">{e.title}</div>
            <div className="sub">{e.message}</div>
            <div className="when">{new Date(e.created_at).toLocaleString()}</div>
          </div>
        ))}
      </div>
      <div className="actions"><button className="sm" onClick={onClose}>Close</button></div>
    </Dialog>
  );
}
