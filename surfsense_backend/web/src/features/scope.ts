/*
 * Folder scope. "Is there anything about X in these two folders?" — the scope is
 * a set of folder ids the server confines retrieval to (each id's whole subtree).
 * It is deliberately sticky: an investigation asks many questions of the same
 * subset. The chip above the composer keeps a sticky scope from being invisible.
 */
import type { FolderNode } from "../api/types";
import { getState, setPersisted } from "../store";
import { toast } from "../ui/toast";

export const ORIGIN_ORDER: Record<FolderNode["origin"], number> = { own: 0, linked: 1, group: 2, user: 3 };

/** How a folder is named outside the tree — in the scope chip and in /scope. */
export const folderLabel = (n: FolderNode) =>
  n.origin === "user" ? `${n.owner_email}/${n.name}`
  : n.origin === "group" ? `${n.group_name}/${n.name}`
  : n.origin === "linked" ? `${n.name} (imported)`
  : n.name;

/** Another session's folder: retrieval hides it here whatever the scope says. */
export const otherSession = (n: FolderNode, threadId: number | null) =>
  n.owner_thread_id != null && n.owner_thread_id !== threadId;

export function setScope(ids: number[], namesById: Record<number, string> = {}) {
  const known = getState().scopeNames;
  const scopeFolderIds = [...new Set(ids)];
  const scopeNames: Record<number, string> = {};
  for (const id of scopeFolderIds) {
    const name = namesById[id] ?? known[id];
    if (name) scopeNames[id] = name;
  }
  setPersisted({ scopeFolderIds, scopeNames });
}

export const clearScope = () => setScope([]);

/** Tick or untick a set of folder ids as one (a folder, or a user's/group's roots). */
export function toggleScope(ids: number[], on: boolean, names: Record<number, string>) {
  const { scopeFolderIds, scopeNames } = getState();
  const rest = scopeFolderIds.filter((id) => !ids.includes(id));
  setScope(on ? [...rest, ...ids] : rest, { ...scopeNames, ...names });
}

/** Drop ids whose folder is gone, so a stale scope can't silently match nothing. */
export function pruneScope(live: Set<number>) {
  const { scopeFolderIds, scopeNames } = getState();
  const kept = scopeFolderIds.filter((id) => live.has(id));
  if (kept.length !== scopeFolderIds.length) {
    setScope(kept, scopeNames);
    toast("Dropped a deleted folder from the question scope.", "warn");
  }
}

export const scopeLabel = () => {
  const { scopeFolderIds, scopeNames } = getState();
  return scopeFolderIds.map((id) => scopeNames[id] ?? `#${id}`).join(", ");
};
