import { api } from "../../api/client";
import { getState, setState } from "../../store";
import { guard } from "../../ui/toast";
import { sessionContext } from "../sessionContext";
import { assistantAnswer, keepTitle, threadHistory } from "../session";
import { addMessage, setStatus, updateMessage } from "./messages";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Accumulates the turn's text blocks as they stream.
 *
 * The stream carries text as text-start / text-delta / text-end triples keyed by
 * a text id, and a fresh block opens after each tool call. Only the last
 * non-empty block is the answer — the same rule assistantAnswer() applies to a
 * reloaded thread, because the backend persists one text part per block.
 */
function textBlocks() {
  const blocks: { id?: string; text: string }[] = [];
  const current = (id?: string) => {
    const cur = blocks[blocks.length - 1];
    if (!cur || (id !== undefined && cur.id !== id)) blocks.push({ id, text: "" });
    return blocks[blocks.length - 1];
  };
  return {
    start: (id?: string) => { current(id); },
    delta: (id: string | undefined, delta: string) => { current(id).text += delta; },
    // An empty block counts for nothing: text-start fires before the first token.
    answer: () => blocks.filter((b) => b.text.trim()).pop()?.text ?? "",
  };
}

interface StreamEvent { type?: string; id?: string; delta?: string; toolName?: string; tool_name?: string }

/** Human-readable label for the pre-answer events, so a silent run is diagnosable. */
function describeEvent(ev: StreamEvent & { type: string }) {
  const name = ev.toolName ?? ev.tool_name ?? "";
  if (ev.type.startsWith("tool")) {
    if (name.includes("search")) return "searching the knowledge base…";
    return name ? `calling ${name}…` : "calling a tool…";
  }
  if (ev.type.includes("thinking") || ev.type.includes("reasoning")) return "thinking…";
  if (ev.type === "task" || ev.type.includes("agent")) return "delegating to the knowledge_base agent…";
  return "working…";
}

/** Mark the composer busy for the length of `fn`. */
export async function whileBusy(fn: () => Promise<void>) {
  setState({ busy: true });
  try { await fn(); } finally { setState({ busy: false }); }
}

/** Run one chat turn: stream the answer, then swap in the persisted, cited copy. */
export async function ask(question: string) {
  if (getState().busy) return;
  await whileBusy(async () => {
    addMessage("user", question);
    const msgId = addMessage("assistant", "", true);
    const blocks = textBlocks();
    const { threadId, spaceId, scopeFolderIds } = getState();

    try {
      const res = await api("POST", "/api/v1/new_chat", {
        json: {
          chat_id: threadId,
          search_space_id: spaceId,
          user_query: question,
          // The server retrieves first and answers in one tool-free call.
          simple_rag: true,
          // Folder scope: the server confines retrieval to these subtrees, so
          // "nothing found" means nothing found *here*.
          mentioned_folder_ids: scopeFolderIds.length ? scopeFolderIds : undefined,
          // The server keeps nothing between turns, so the note rides along every time.
          session_context: sessionContext() || undefined,
        },
      });

      // The AI SDK stream protocol: `data: {json}` lines, ending with `data: [DONE]`.
      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).replace(/\r$/, "");
          buf = buf.slice(nl + 1);
          if (!line.startsWith("data:")) continue;
          const payload = line.slice(5).trim();
          if (!payload || payload === "[DONE]") continue;

          let ev: StreamEvent;
          try { ev = JSON.parse(payload); } catch { continue; }

          if (ev.type === "text-start") {
            blocks.start(ev.id);
          } else if (ev.type === "text-delta" && typeof ev.delta === "string") {
            blocks.delta(ev.id, ev.delta);
            updateMessage(msgId, { text: blocks.answer() });
          } else if (!blocks.answer() && typeof ev.type === "string") {
            setStatus(msgId, describeEvent(ev as StreamEvent & { type: string }));
          }
        }
      }

      if (!blocks.answer()) {
        setStatus(msgId, "The stream carried no answer. Usually: the model errored before " +
          "replying, or the chat role isn't wired to a working key.", "err");
      } else {
        setStatus(msgId, null);
        await settleAnswer(msgId, blocks.answer());
      }
    } catch (e) {
      setStatus(msgId, e instanceof Error ? e.message : String(e), "err");
    } finally {
      updateMessage(msgId, { streaming: false });
      // The first reply in a thread gets auto-titled server-side; keep our name.
      guard(keepTitle);
    }
  });
}

/**
 * Re-render the finished answer from the *persisted* message.
 *
 * The model cites with bare ordinals — [1], [2] — and the backend rewrites them
 * into [citation:<chunk id>] markers when the turn is persisted, NOT on the live
 * stream. So only the stored copy has real citations; swap it in once it lands.
 * Persistence can trail the last byte by a moment: retry once before giving up.
 */
async function settleAnswer(msgId: number, streamed: string) {
  const ORDINALS = /\[\s*\d+(?:\s*[-–—,]\s*\d+)*\s*\]/;
  const hasOrdinals = ORDINALS.test(streamed);

  for (const wait of [0, 700]) {
    if (wait) await sleep(wait);
    const history = await threadHistory(getState().threadId);
    const last = (history?.messages ?? []).filter((m) => m.role === "assistant").pop();
    const stored = last ? assistantAnswer(last.content) : "";
    if (!stored.trim()) continue;

    const settled = !hasOrdinals || stored.includes("[citation:") || !ORDINALS.test(stored);
    if (settled || wait) {
      updateMessage(msgId, { text: stored });
      return;
    }
  }
}
