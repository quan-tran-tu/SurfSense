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
 * A client-side note in the message flow, like a template's outline. Not sent to
 * the model and not persisted, so a reload drops it.
 */
export const addSystemNote = (text: string) => addMessage("system", text);

export const clearMessages = () => setState({ messages: [] });
