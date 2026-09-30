import { create } from "zustand";
import { api, apiJson, ApiError } from "../api/client";
import type { FolderDocument, FolderLink, FolderNode, WatchedFolder } from "../api/types";
import { refreshFolders } from "../queryClient";
import { getState, setPersisted } from "../store";
import { toast } from "../ui/toast";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/* ------------------------------------------------------------------ reads */

export const loadTree = () =>
  apiJson<FolderNode[]>("GET", `/api/v1/search-spaces/${getState().spaceId}/folder-tree`);

export const folderDocs = (folderId: number) =>
  apiJson<FolderDocument[]>("GET", `/api/v1/search-spaces/${getState().spaceId}/folders/${folderId}/documents`);

/**
 * An imported folder lives in the *sharer's* search space — we only hold a link
 * to it, so it is listed on its own rather than under Folders.
 */
export const listImports = () =>
  apiJson<FolderLink[]>("GET", `/api/v1/search-spaces/${getState().spaceId}/folder-links`);

const watchedFolders = () =>
  apiJson<WatchedFolder[]>("GET", `/api/v1/documents/watched-folders?search_space_id=${getState().spaceId}`);

const allFolders = () =>
  apiJson<{ id: number; parent_id: number | null }[]>("GET", `/api/v1/folders?search_space_id=${getState().spaceId}`);

/** folder_id filtering is exact, not recursive — walk the tree ourselves. */
async function subtreeIds(rootId: number) {
  const all = (await allFolders()) ?? [];
  const ids = new Set([rootId]);
  for (let grew = true; grew;) {
    grew = false;
    for (const f of all) {
      if (f.parent_id != null && ids.has(f.parent_id) && !ids.has(f.id)) { ids.add(f.id); grew = true; }
    }
  }
  return ids;
}

async function docsIn(ids: Set<number>) {
  const page = await apiJson<{ items?: (FolderDocument & { folder_id: number | null })[] }>("GET",
    `/api/v1/documents?search_space_id=${getState().spaceId}&document_types=LOCAL_FOLDER_FILE&page_size=-1`);
  return (page?.items ?? []).filter((d) => d.folder_id != null && ids.has(d.folder_id));
}

/* ----------------------------------------------------------------- ingest */

const MAX_FILE_BYTES = 500 * 1024 * 1024;
const BATCH = 20;
const POLL_MS = 3000, TIMEOUT_MS = 1_800_000, STABLE_POLLS = 2;
// How long to wait for the FIRST document row before concluding nobody is making
// them. Generous: it only has to cover the worker picking the job off the queue.
const NO_DOCS_GRACE_MS = 60_000;

const WORKER_HINT =
  "/documents/folder-upload only queues a Celery task — the worker is what creates " +
  "the documents, so an upload can succeed while nothing is ever indexed.\n\n" +
  "Start it in surfsense_backend/:\n\n" +
  "  uv run celery -A celery_worker.celery_app worker --loglevel=info \\\n" +
  "    --concurrency=1 --pool=solo --queues=surfsense,surfsense.connectors";

/** Upload progress, shown under the Folders panel while an ingest runs. */
export const useIngest = create<{ text: string; frac: number } | { text: null; frac: 0 }>(
  () => ({ text: null, frac: 0 }));

/**
 * Blocks until every document under `rootId` has settled. Celery keeps inserting
 * rows after the upload returns, so "nothing is processing" only counts once the
 * numbers hold still for STABLE_POLLS consecutive reads.
 */
async function waitReady(
  rootId: number,
  onProgress: (ready: number, busy: number, failed: number, total: number) => void,
) {
  let waited = 0, stable = 0, last = "";
  while (waited < TIMEOUT_MS) {
    const docs = await docsIn(await subtreeIds(rootId));
    const ready = docs.filter((d) => d.status?.state === "ready").length;
    const failed = docs.filter((d) => d.status?.state === "failed").length;
    const busy = docs.filter((d) => ["processing", "deleting"].includes(d.status?.state ?? "")).length;
    onProgress(ready, busy, failed, docs.length);

    // No rows at all, well past the time the worker needed to claim the job: it
    // isn't running. Say so instead of polling in silence until TIMEOUT_MS.
    if (docs.length === 0 && waited >= NO_DOCS_GRACE_MS) {
      throw new ApiError(0,
        `The upload was accepted, but ${Math.round(waited / 1000)}s later not one ` +
        `document exists for this folder.\n\n${WORKER_HINT}`);
    }
    if (docs.length > 0 && busy === 0) {
      const snap = `${docs.length}:${ready}:${failed}`;
      if (snap === last) { if (++stable >= STABLE_POLLS) return { ready, failed }; }
      else stable = 0;
      last = snap;
    } else {
      last = `${docs.length}:${ready}:${failed}`;
    }
    await sleep(POLL_MS);
    waited += POLL_MS;
  }
  throw new ApiError(0, `Timed out after ${TIMEOUT_MS / 1000}s with indexing unfinished.\n\n${WORKER_HINT}`);
}

/**
 * The browser hands us a folder as a flat FileList whose webkitRelativePath is
 * "<root>/<sub>/<file>". The first segment is the folder name — everything after
 * it is the relative path the backend wants. Skip dotfiles and oversized files.
 */
export function collectFiles(fileList: File[]) {
  const files: File[] = [], paths: string[] = [];
  let root = "";
  for (const f of fileList) {
    const segs = (f.webkitRelativePath || f.name).split("/");
    if (!root) root = segs[0];
    if (segs.some((s) => s.startsWith("."))) continue;
    if (f.size > MAX_FILE_BYTES) continue;
    files.push(f);
    paths.push(segs.slice(1).join("/"));
  }
  return { root, files, paths };
}

/**
 * Upload a picked folder and wait for it to index. `sessionOnly` scopes it to the
 * open session: the backend hides it from every other session until ⤴ promotes it.
 */
export async function ingest(fileList: File[], sessionOnly: boolean) {
  const { root, files, paths } = collectFiles(fileList);
  if (!files.length) { toast("No indexable files in that folder.", "warn"); return; }
  const { spaceId } = getState();
  const threadId = sessionOnly ? getState().threadId : null;

  // The KB label is the basename, never a full disk path: it becomes Folder.name,
  // which the agent sees as /documents/<name>/… The duplicate check matches scope
  // too: another session's "root" is a different folder, not a rebuild.
  const existing = ((await watchedFolders()) ?? []).find(
    (f) => f.name === root && (f.owner_thread_id ?? null) === threadId);
  if (existing && !confirm(`"${root}" is already indexed${threadId ? " in this session" : ""}. Rebuild it? Its documents will be deleted and re-extracted.`)) return;

  const progress = (text: string, frac: number) => useIngest.setState({ text, frac });
  try {
    if (existing) { progress(`removing old "${root}"…`, 0); await deleteFolder(existing.id); }

    let rootId: number | null = null;
    for (let i = 0; i < files.length; i += BATCH) {
      const chunk = files.slice(i, i + BATCH);
      const fd = new FormData();
      fd.append("folder_name", root);
      fd.append("search_space_id", String(spaceId));
      fd.append("relative_paths", JSON.stringify(paths.slice(i, i + BATCH)));
      if (threadId) fd.append("thread_id", String(threadId));
      if (rootId) fd.append("root_folder_id", String(rootId));
      for (const f of chunk) fd.append("files", f, f.name);

      const res = await apiJson<{ root_folder_id?: number }>("POST", "/api/v1/documents/folder-upload", { body: fd });
      rootId ??= res?.root_folder_id ?? null;
      if (!rootId) throw new ApiError(0, "folder-upload returned no root_folder_id");
      progress(`uploading ${Math.min(i + BATCH, files.length)}/${files.length}…`, (i + chunk.length) / files.length * 0.5);
    }

    // Drop documents for files that are no longer on disk.
    await api("POST", "/api/v1/documents/folder-sync-finalize", {
      json: { folder_name: root, search_space_id: spaceId, root_folder_id: rootId, all_relative_paths: paths },
    });

    const { ready, failed } = await waitReady(rootId!, (r, busy, bad, total) =>
      progress(
        total === 0
          ? "queued — waiting for the indexer to pick it up…"
          : `indexing ${r}/${files.length} · ${busy} processing · ${bad} failed`,
        0.5 + (r / files.length) * 0.5));

    if (ready < files.length) {
      const missing = files.length - ready - failed;
      toast(`Indexed ${ready}/${files.length} in "${root}". ${failed} failed; ` +
        `${missing} produced no document (unsupported type, or identical content already indexed).`, "warn", 12000);
    } else {
      toast(`Indexed ${ready} file(s) into "${root}"` +
        (threadId ? " (visible only in this session — ⤴ in Folders makes it space-wide)." : "."), "ok", 9000);
    }
  } catch (e) {
    toast(`Ingest of "${root}" failed: ${e instanceof Error ? e.message : e}`, "err", 12000);
  } finally {
    useIngest.setState({ text: null, frac: 0 });
    await refreshFolders();
  }
}

/* ---------------------------------------------------------------- actions */

export async function deleteFolder(id: number) {
  await api("DELETE", `/api/v1/folders/${id}`);
  for (let waited = 0; waited < TIMEOUT_MS; waited += POLL_MS) {
    const all = (await allFolders()) ?? [];
    if (!all.some((f) => f.id === id)) return;
    await sleep(POLL_MS);
  }
  throw new ApiError(0, `folder #${id} is still present after ${TIMEOUT_MS / 1000}s`);
}

export async function promoteFolder(f: FolderNode) {
  await api("PATCH", `/api/v1/folders/${f.id}/scope`, { json: { scope: "space" } });
  toast(`"${f.name}" is now space-wide — every session sees it.`, "ok", 8000);
  await refreshFolders();
}

/** Mint a share token for a space-wide folder; `expiresAt` null means it never expires. */
export async function shareFolder(path: string, expiresAt: string | null) {
  const { token } = await apiJson<{ token: string }>(
    "POST", `/api/v1/search-spaces/${getState().spaceId}/folder-shares`,
    { json: { path, expires_at: expiresAt ?? undefined } });
  setPersisted({ shares: [...getState().shares, { path, token, expiresAt }] });
  toast(`Share token for "${path}" created. Hand it to the other user — they paste it under Imported.`, "ok", 10000);
}

export async function unshareFolder(token: string) {
  await api("DELETE", `/api/v1/folder-shares/${token}`);
  setPersisted({ shares: getState().shares.filter((s) => s.token !== token) });
  toast("Revoked. Importers lose access on their next query.", "ok");
}

export async function importFolder(token: string) {
  const { folder_name } = await apiJson<{ folder_name: string }>(
    "POST", `/api/v1/search-spaces/${getState().spaceId}/folder-links`, { json: { token } });
  toast(`Imported "${folder_name}" — it is searchable now, and readable (read-only) at /documents/_shared/${folder_name}.`, "ok", 10000);
  await refreshFolders();
}

/** Drops our link. The sharer's folder and documents are untouched. */
export async function removeImport(link: FolderLink) {
  await api("DELETE", `/api/v1/folder-links/${link.id}`);
  toast(`Removed "${link.folder_name}" — it is no longer searchable here. The owner's copy is untouched.`);
  await refreshFolders();
}
