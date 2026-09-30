import { create } from "zustand";
import {
  DEFAULT_BACKEND, DEFAULT_MODEL, DEFAULT_MODEL_BASE,
  HAS_BACKEND, INJECTED_BACKEND, INJECTED_BASE, INJECTED_KEY, INJECTED_MODEL,
} from "./config";
import type { Share, Template } from "./api/types";

/* ---------------------------------------------------------------- messages */

export type MessageRole = "user" | "assistant" | "system";
export type StatusKind = "" | "err" | "warn";

export interface ChatMessage {
  id: number;
  role: MessageRole;
  text: string;
  streaming?: boolean;
  status?: string;
  statusKind?: StatusKind;
  links?: { label: string; href: string }[];   // shown under the bubble, open in a new tab
}

/** What the right-hand panel shows: a cited chunk in context, or a whole document. */
export type SourceView =
  | { kind: "chunk"; chunkId: string; ordinal: string; key: string }
  | { kind: "document"; id: number; title: string };

/* ------------------------------------------------------------------- state */

export interface State {
  backend: string;
  email: string;
  password: string;
  apiKey: string;
  model: string;
  modelBase: string;

  signedIn: boolean;
  // "resuming" while a reload tries the session cookies before showing sign-in.
  boot: "resuming" | "ready";
  authNotice: string;          // why the sign-in screen is showing (session expired, …)
  spaceId: number | null;
  threadId: number | null;
  threadTitle: string;
  lastReportId: number | null; // most recent report in this thread; the default for /export and /revise
  isAdmin: boolean;            // is_superuser — gates the Admin page (the server enforces it too)
  busy: boolean;
  adminOpen: boolean;

  // Per-user, persisted under byUser[email] (see savePrefs).
  shares: Share[];             // the backend has no "my shares" list endpoint, so we remember
  templates: Template[];       // report format exemplars, browser-only
  // Folder ids every question and report is confined to; [] = the whole space.
  // Sent as mentioned_folder_ids (/new_chat) and folder_ids (/reports/generate),
  // where the server matches each id's whole subtree.
  scopeFolderIds: number[];
  scopeNames: Record<number, string>;
  // thread id -> the user's note for that session: facts the answers must take
  // as true. Sent as session_context with every question; never stored server-side.
  sessionNotes: Record<number, string>;

  // In-scope folders another session owns. Retrieval hides those in this session
  // whatever the scope says, so the chip warns rather than let it read as
  // "nothing found". Derived from the folder tree; not persisted.
  unreachableScopeIds: number[];

  messages: ChatMessage[];
  source: SourceView | null;
}

export const useStore = create<State>(() => ({
  backend: DEFAULT_BACKEND,
  email: "", password: "", apiKey: "",
  model: DEFAULT_MODEL, modelBase: DEFAULT_MODEL_BASE,
  signedIn: false, boot: "ready", authNotice: "",
  spaceId: null, threadId: null, threadTitle: "",
  lastReportId: null, isAdmin: false, busy: false, adminOpen: false,
  shares: [], templates: [], scopeFolderIds: [], scopeNames: {}, sessionNotes: {},
  unreachableScopeIds: [], messages: [], source: null,
}));

export const getState = useStore.getState;
export const setState = useStore.setState;

/* ------------------------------------------------------------------- prefs */
//
// The same localStorage key and layout the single-file client used, so shares,
// templates and session notes survive the move to this build.

const LS = "surfsense.mini";

interface UserPrefs {
  shares?: Share[];
  threadId?: number | null;
  threadTitle?: string;
  templates?: Template[];
  scopeFolderIds?: number[];
  scopeNames?: Record<number, string>;
  sessionNotes?: Record<number, string>;
}

interface Prefs {
  backend?: string;
  email?: string;
  apiKey?: string;
  model?: string;
  modelBase?: string;
  byUser?: Record<string, UserPrefs>;
  // pre-byUser layout
  shares?: Share[];
  threadId?: number | null;
  threadTitle?: string;
}

const readPrefs = (): Prefs => {
  try { return JSON.parse(localStorage.getItem(LS) || "{}"); } catch { return {}; }
};

/**
 * Preferences are shared by everyone using this browser, but per-user state must
 * not be: a share token is a capability — anyone holding it can import that folder
 * — and a thread id belongs to one user's search space. So they live under
 * byUser[email], and signing in adopts only that user's slice.
 */
export function savePrefs() {
  const s = getState();
  const prefs = readPrefs();
  const byUser = prefs.byUser ?? {};
  if (s.email) {
    byUser[s.email] = {
      shares: s.shares,
      threadId: s.threadId,
      threadTitle: s.threadTitle,
      templates: s.templates,
      scopeFolderIds: s.scopeFolderIds,
      scopeNames: s.scopeNames,
      sessionNotes: s.sessionNotes,
    };
  }
  try {
    localStorage.setItem(LS, JSON.stringify({
      // undefined drops the key, so a served value is never stored.
      backend: HAS_BACKEND ? undefined : s.backend,
      email: s.email,
      apiKey: INJECTED_KEY ? "" : s.apiKey,
      model: INJECTED_MODEL ? "" : s.model,
      modelBase: INJECTED_BASE ? "" : s.modelBase,
      byUser,
    }));
  } catch { /* private window or full storage: prefs are a convenience */ }
}

/** Take `email`'s slice of the prefs (nothing, if new). */
export function adoptUser(email: string) {
  const mine = readPrefs().byUser?.[email] ?? {};
  setState({
    shares: mine.shares ?? [],
    threadId: mine.threadId ?? null,
    threadTitle: mine.threadTitle ?? "",
    templates: mine.templates ?? [],
    scopeFolderIds: mine.scopeFolderIds ?? [],
    scopeNames: mine.scopeNames ?? {},
    sessionNotes: mine.sessionNotes ?? {},
  });
}

export function loadPrefs() {
  const prefs = readPrefs();
  const { byUser, shares, threadId, threadTitle, ...shared } = prefs;
  setState({
    email: shared.email ?? "",
    apiKey: shared.apiKey ?? "",
    backend: shared.backend ?? "",
    model: shared.model ?? "",
    modelBase: shared.modelBase ?? "",
  });

  // Migrate the pre-byUser layout, where these sat at the top level and leaked
  // across accounts. Attribute them to the user who was last signed in.
  if (!byUser && prefs.email && (shares || threadId)) {
    setState({ shares: shares ?? [], threadId: threadId ?? null, threadTitle: threadTitle ?? "" });
    savePrefs();
  }

  adoptUser(getState().email);
  // Served values always win; empty leftovers fall back to the DeepSeek defaults.
  const s = getState();
  setState({
    backend: HAS_BACKEND ? INJECTED_BACKEND : s.backend || DEFAULT_BACKEND,
    apiKey: INJECTED_KEY || s.apiKey,
    model: INJECTED_MODEL || s.model || DEFAULT_MODEL,
    modelBase: INJECTED_BASE || s.modelBase || DEFAULT_MODEL_BASE,
  });
}

/** Set per-user state and persist it in one go. */
export function setPersisted(patch: Partial<State>) {
  setState(patch);
  savePrefs();
}
