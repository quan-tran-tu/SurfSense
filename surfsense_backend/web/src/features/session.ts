import { api, apiJson, ApiError, setAdminLostHandler, setSessionLostHandler } from "../api/client";
import type { Thread } from "../api/types";
import { keys, queryClient, refreshFolders } from "../queryClient";
import { getState, savePrefs, setState } from "../store";
import { toast } from "../ui/toast";
import { addMessage, clearMessages } from "./chat/messages";

/* ------------------------------------------------------------------ space */

/**
 * One space per user, named cli:<email> — the same space ask.sh uses, so the two
 * clients see one knowledge base. A search space is the server-enforced access
 * boundary; the user never names one by id.
 */
export async function ensureSpace() {
  const want = `cli:${getState().email}`;
  const spaces = await apiJson<{ id: number; name: string }[]>(
    "GET", "/api/v1/searchspaces?limit=200&skip=0&owned_only=true");
  const found = (spaces || []).find((s) => s.name === want);
  if (found) { setState({ spaceId: found.id }); return; }

  const made = await apiJson<{ id: number }>("POST", "/api/v1/searchspaces", {
    json: { name: want, description: "Private CLI knowledge base" },
  });
  setState({ spaceId: made.id });
  toast(`Created your private search space #${made.id}`);
}

interface ModelConnection {
  id: number;
  base_url: string;
  search_space_id: number | null;
  models?: { id: number; model_id: string }[];
}

/**
 * Create-or-update the model connection, then point the space's *chat role* at
 * the model row it produced. Both halves are required: a connection alone leaves
 * the chat role unset and every question fails. provider=openai + an explicit
 * base_url keeps the key strictly per-connection.
 */
export async function ensureModel() {
  const { spaceId, modelBase, model, apiKey } = getState();
  const conns = await apiJson<ModelConnection[]>("GET", `/api/v1/model-connections?search_space_id=${spaceId}`);
  const conn = (conns || []).find((c) => c.base_url === modelBase && c.search_space_id != null);

  let modelRowId: number | null = null;
  if (conn) {
    await api("PUT", `/api/v1/model-connections/${conn.id}`, { json: { api_key: apiKey, enabled: true } });
    modelRowId = (conn.models || []).find((m) => m.model_id === model)?.id ?? null;
  }

  if (!modelRowId) {
    // The connection exists but carries no row for this model id — rebuild it clean.
    if (conn) await api("DELETE", `/api/v1/model-connections/${conn.id}`);
    const made = await apiJson<ModelConnection>("POST", "/api/v1/model-connections", {
      json: {
        provider: "openai", base_url: modelBase, api_key: apiKey,
        scope: "SEARCH_SPACE", search_space_id: spaceId, enabled: true,
        models: [{
          model_id: model, display_name: model, source: "MANUAL",
          supports_chat: true, supports_tools: true, enabled: true,
        }],
      },
    });
    modelRowId = (made.models || []).find((m) => m.model_id === model)?.id ?? null;
    if (!modelRowId) throw new ApiError(0, `connection created, but it has no model row for "${model}"`);
  }

  const roles = await apiJson<{ chat_model_id?: number }>("GET", `/api/v1/search-spaces/${spaceId}/model-roles`);
  if (roles?.chat_model_id !== modelRowId) {
    await api("PUT", `/api/v1/search-spaces/${spaceId}/model-roles`, { json: { chat_model_id: modelRowId } });
  }
}

/** Read is_superuser off /users/me; the Admin button follows it. */
export async function detectAdmin() {
  let admin = false;
  try {
    const me = await apiJson<{ is_superuser?: boolean }>("GET", "/users/me");
    admin = !!me?.is_superuser;
  } catch { /* not an admin as far as this page can tell */ }
  if (getState().isAdmin && !admin) loseAdmin();
  else setState({ isAdmin: admin });
}

/**
 * Admin access was revoked while this page was open. Nothing admin-only may
 * stay on screen: the admin page closes, the button goes, and the folder tree
 * drops the other users' folders it was showing (the server stops returning
 * them; the refetch makes the tree and the scope follow).
 */
export function loseAdmin() {
  if (!getState().isAdmin) return;
  setState({ isAdmin: false, adminOpen: false });
  if (location.hash.startsWith("#admin")) history.replaceState(null, "", location.pathname + location.search);
  queryClient.removeQueries({ queryKey: keys.admin });
  toast("Your admin access has been removed — back to chat.", "warn", 10000);
  void refreshFolders();
}
setAdminLostHandler(loseAdmin);

// The session is gone and could not be renewed: back to the sign-in screen,
// saying why, with nothing of the previous session left on screen.
setSessionLostHandler(() => {
  if (!getState().signedIn) return;
  queryClient.clear();
  setState({
    signedIn: false, adminOpen: false, isAdmin: false, password: "", messages: [], source: null,
    authNotice: "Your session has expired. Sign in again.",
  });
});

/* ---------------------------------------------------------------- threads */

export async function listThreads(): Promise<Thread[]> {
  const r = await apiJson<{ threads?: Thread[]; items?: Thread[] } | Thread[]>(
    "GET", `/api/v1/threads?search_space_id=${getState().spaceId}`);
  if (Array.isArray(r)) return r;
  return r?.threads ?? r?.items ?? [];
}

/**
 * Open a thread by id.
 *
 * A session is a thread *id*, never a title. Titles are not stable: the backend
 * auto-generates one from the first exchange (title_gen.py) and overwrites
 * whatever we set, so looking a session up by title would silently fork a new
 * one. keepTitle() puts our name back, but the id is what we navigate by.
 */
export async function openThread(thread: Thread) {
  setState({
    threadId: thread.id,
    threadTitle: thread.title,
    lastReportId: null,   // the /export|/revise default is per-thread
    source: null,
  });
  savePrefs();
  // The folder tree too: the "this session" badges depend on which thread is open.
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: keys.threads(getState().spaceId) }),
    loadHistory(),
    refreshFolders(),
  ]);
}

/**
 * The name is optional. An unnamed session is opened with an empty threadTitle
 * so keepTitle() stays out of the way and the backend's auto-titler names it
 * from the first exchange.
 */
export async function createSession(title: string) {
  const json: Record<string, unknown> = { search_space_id: getState().spaceId };
  if (title) json.title = title;
  const thread = await apiJson<Thread>("POST", "/api/v1/threads", { json });
  await openThread(title ? thread : { ...thread, title: "" });
}

/** Resume the last thread we were in; fall back to the newest, else start one. */
export async function resumeSession() {
  const threads = await listThreads();
  const mine = threads.find((t) => t.id === getState().threadId) ?? threads[0];
  if (mine) await openThread(mine);
  else await createSession("");
}

/**
 * Deleting the session you're in leaves nowhere to be, so land somewhere sane:
 * the newest surviving thread, or a fresh one if that was the last.
 */
export async function deleteSession(thread: Thread) {
  await api("DELETE", `/api/v1/threads/${thread.id}`);
  toast(`Deleted session "${thread.title}".`);
  // The note belonged to that session; don't leave it behind in the prefs.
  const { [thread.id]: _gone, ...sessionNotes } = getState().sessionNotes;
  setState({ sessionNotes, threadId: thread.id === getState().threadId ? null : getState().threadId });
  savePrefs();
  await resumeSession();
}

/**
 * The backend auto-titles a thread from its first exchange, which would silently
 * rename the session the user named. Put our name back. Unnamed sessions skip
 * the pin but still refresh the list so the new title appears.
 */
export async function keepTitle() {
  const { threadTitle, threadId, spaceId } = getState();
  if (threadTitle) {
    const mine = (await listThreads()).find((t) => t.id === threadId);
    if (mine && mine.title !== threadTitle) {
      await api("PUT", `/api/v1/threads/${threadId}`, { json: { title: threadTitle } });
    }
  }
  await queryClient.invalidateQueries({ queryKey: keys.threads(spaceId) });
}

/* ---------------------------------------------------------------- history */

type Content = string | { type?: string; text?: string }[] | { text?: string } | null;

/**
 * Assistant-ui stores message content as JSONB: a plain string on some rows, an
 * array of typed parts on others. Take the text and ignore the rest.
 */
export function messageText(content: Content): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.filter((p) => p?.type === "text" && typeof p.text === "string")
      .map((p) => p.text).join("");
  }
  if (content && typeof content.text === "string") return content.text;
  return "";
}

/**
 * The answer inside a stored assistant message, on the same rule the live stream
 * uses: the backend starts a fresh text part after every tool call, so the last
 * non-empty text part is the answer; the narration before it is dropped.
 */
export function assistantAnswer(content: Content): string {
  const blocks = Array.isArray(content)
    ? content.filter((p) => p?.type === "text" && typeof p.text === "string")
      .map((p) => p.text as string).filter((t) => t.trim())
    : [messageText(content)].filter((t) => t.trim());
  return blocks.pop() ?? "";
}

/**
 * Questions go to the backend wrapped in a directive; show only what the user
 * typed. Match either wrapper so reloaded history stays clean.
 */
const unwrapQuestion = (text: string) =>
  text.match(/<question>\n([\s\S]*?)\n<\/question>/)?.[1]
  ?? text.match(/<report_request>\n([\s\S]*?)\n<\/report_request>/)?.[1]
  ?? text;

export interface StoredMessage { role: string; content: Content }

export const threadHistory = (threadId: number | null) =>
  apiJson<{ messages?: StoredMessage[] }>("GET", `/api/v1/threads/${threadId}`);

export async function loadHistory() {
  clearMessages();
  const history = await threadHistory(getState().threadId);
  for (const m of history?.messages ?? []) {
    if (m.role === "user") {
      const text = messageText(m.content);
      if (text.trim()) addMessage("user", unwrapQuestion(text));
    } else if (m.role === "assistant") {
      const answer = assistantAnswer(m.content);
      if (answer.trim()) addMessage("assistant", answer);
    }
  }
}

/* -------------------------------------------------------------- bootstrap */

/**
 * On page load: if this browser still holds a session (or a refresh cookie that
 * can renew one), carry on where the user left off instead of asking them to
 * sign in again. The API key must be known too — served, or remembered.
 */
export async function resume() {
  const { email, apiKey } = getState();
  if (!email || !apiKey) return;
  setState({ boot: "resuming" });
  try {
    const me = await apiJson<{ email?: string }>("GET", "/users/me");
    // The cookie is for whoever signed in last in this browser; only resume as them.
    if (me?.email?.toLowerCase() !== email.toLowerCase()) return;
    await start();
  } catch {
    setState({ signedIn: false, authNotice: "" });
  } finally {
    setState({ boot: "ready" });
  }
}

export async function start() {
  await ensureSpace();
  await ensureModel();
  await resumeSession();
  await detectAdmin();
  setState({ signedIn: true });
  // "#admin" survives a reload, so an admin lands back on the page they left.
  if (getState().isAdmin && location.hash.startsWith("#admin")) setState({ adminOpen: true });
}
