/*
 * Facts the user hands the model for one session — an official's new post, an
 * event newer than the documents. Kept per thread in the per-user prefs and sent
 * with every turn: the server stores nothing, so nothing can go stale there, and
 * Clear takes effect on the very next question.
 */
import { getState, setPersisted } from "../store";

export const SESSION_CONTEXT_MAX = 8000;   // NewChatRequest.session_context max_length

export const sessionContext = () => {
  const { sessionNotes, threadId } = getState();
  return (threadId != null ? sessionNotes[threadId] ?? "" : "").trim();
};

export function setSessionContext(text: string) {
  const { sessionNotes, threadId } = getState();
  if (threadId == null) return;
  const note = text.trim();
  const next = { ...sessionNotes };
  if (note) next[threadId] = note;
  else delete next[threadId];
  setPersisted({ sessionNotes: next });
}
