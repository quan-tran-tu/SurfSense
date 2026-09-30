import { getState, setState, type ChatMessage, type MessageRole, type StatusKind } from "../../store";

let nextId = 1;

export function addMessage(role: MessageRole, text: string, streaming = false): number {
  const id = nextId++;
  setState({ messages: [...getState().messages, { id, role, text, streaming }] });
  return id;
}

export function updateMessage(id: number, patch: Partial<ChatMessage>) {
  setState({ messages: getState().messages.map((m) => (m.id === id ? { ...m, ...patch } : m)) });
}

/** A status line under a message; null clears it. */
export const setStatus = (id: number, status: string | null, kind: StatusKind = "") =>
  updateMessage(id, { status: status ?? "", statusKind: kind });

/**
 * A client-side note in the message flow — command output like "report ready" or
 * /help. Not sent to the model and not persisted; /reports re-derives report state
 * from the server, so a reload dropping these notes loses nothing.
 */
export function addSystemNote(text: string, links?: ChatMessage["links"]) {
  const id = addMessage("system", text);
  if (links?.length) updateMessage(id, { links });
  return id;
}

export const clearMessages = () => setState({ messages: [] });
