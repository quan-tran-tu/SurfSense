/*
 * Slash commands typed into the composer. None of them go through the chat
 * agent, so every command works on whatever model the space is pinned to.
 */
import { keys, queryClient } from "../../queryClient";
import { getState } from "../../store";
import { toast } from "../../ui/toast";
import { loadTree } from "../folders";
import { clearScope, folderLabel, ORIGIN_ORDER, otherSession, setScope } from "../scope";
import { SESSION_CONTEXT_MAX, sessionContext, setSessionContext } from "../sessionContext";
import { addSystemNote } from "./messages";
import { exportCommand, listReportsCommand, reportCommand, reviseCommand } from "./reports";

/** Parse "/cmd rest" and dispatch. Unknown commands toast rather than hit the model. */
export async function runCommand(line: string) {
  const sp = line.indexOf(" ");
  const cmd = (sp === -1 ? line : line.slice(0, sp)).toLowerCase();
  const arg = sp === -1 ? "" : line.slice(sp + 1).trim();
  switch (cmd) {
    case "/report": return reportCommand(arg);
    case "/revise": return reviseCommand(arg);
    case "/export": return exportCommand(arg);
    case "/scope": return scopeCommand(arg);
    case "/context": return contextCommand(arg);
    case "/reports": return listReportsCommand();
    case "/templates": return listTemplatesCommand();
    case "/help": return helpCommand();
    default:
      toast(`Unknown command ${cmd}. Try /help.`, "warn");
  }
}

/**
 * Show or set the folder scope from the composer — the keyboard path to the same
 * state the sidebar checkboxes hold.
 *
 *   /scope           list every folder, marking the ones questions are confined to
 *   /scope 3,5       confine questions and reports to folders #3 and #5
 *   /scope all       drop the scope and search the whole space again
 */
async function scopeCommand(arg: string) {
  // Everything a scope can name: own, imported and — for an admin — every
  // user's folders, subfolders included. Fetched fresh, like the tree.
  const nodes = await queryClient.fetchQuery({
    queryKey: keys.tree(getState().spaceId), queryFn: loadTree, staleTime: 0,
  }) ?? [];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const { threadId } = getState();
  const want = arg.trim().toLowerCase();

  if (["all", "none", "off", "clear"].includes(want)) {
    clearScope();
    addSystemNote("Scope cleared — questions and reports search the whole space again.");
    return;
  }

  if (want) {
    const ids = want.split(/[\s,]+/).filter(Boolean).map(Number);
    // An id that names no folder would scope the search to nothing.
    const bad = ids.filter((id) => !Number.isInteger(id) || !byId.has(id));
    if (bad.length) { toast(`No folder with id ${bad.join(", ")}. /scope lists what you have.`, "warn"); return; }
    const elsewhere = ids.filter((id) => otherSession(byId.get(id)!, threadId));
    if (elsewhere.length) {
      toast(`Folder ${elsewhere.join(", ")} belongs to another session and can't ` +
        "answer here. Promote it (⤴) first, or pick another.", "warn", 10000);
      return;
    }
    setScope(ids, Object.fromEntries(ids.map((id) => [id, folderLabel(byId.get(id)!)])));
  }

  const { scopeFolderIds } = getState();
  const inScope = (id: number) => scopeFolderIds.includes(id);
  // The roots, plus any subfolder already in scope; the sidebar tree shows the rest.
  const candidates = nodes
    .filter((n) => n.parent_id == null || inScope(n.id))
    .sort((a, b) => ORIGIN_ORDER[a.origin] - ORIGIN_ORDER[b.origin] ||
      folderLabel(a).localeCompare(folderLabel(b)));
  if (!candidates.length) { addSystemNote("No folders indexed yet — nothing to scope to."); return; }

  addSystemNote("Question scope:\n" +
    candidates.map((n) =>
      `  ${inScope(n.id) ? "[x]" : "[ ]"} #${n.id} — ${folderLabel(n)}` +
      (otherSession(n, threadId) ? "   (another session's — unusable here)" : "")).join("\n") +
    (scopeFolderIds.length
      ? `\n\nQuestions and reports use those ${scopeFolderIds.length} folder(s) only` +
        " — everything below them included. /scope all clears it."
      : "\n\nNothing selected: the whole space is searched. /scope <id,id> narrows it."));
}

function listTemplatesCommand() {
  const { templates } = getState();
  if (!templates.length) {
    addSystemNote("No report templates uploaded yet. Add one in the sidebar " +
      "(Report templates → + Add): any sample report the server can convert — " +
      ".docx is preferred, .pdf, .md and .txt work too.");
    return;
  }
  addSystemNote("Report templates:\n" +
    templates.map((t) => `t${t.id} — ${t.name} (${t.outline.length ? `${t.outline.length} sections` : "style only"})`).join("\n") +
    "\n\nUse one: /report t<id> <what the report should cover>");
}

/**
 *   /context             show the session's note
 *   /context <facts>     add a line to it
 *   /context clear       remove it
 */
function contextCommand(arg: string) {
  const text = arg.trim();
  const current = sessionContext();

  if (!text) {
    addSystemNote(current
      ? `Session context (sent with every question and report in this session):\n\n${current}\n\n` +
        "/context <facts> adds a line · /context clear removes it"
      : "No session context. Add facts your documents don't have yet, e.g.\n" +
        "  /context Since 08/2026, Nguyễn Văn A is Minister of Finance.");
    return;
  }
  if (text.toLowerCase() === "clear") {
    setSessionContext("");
    addSystemNote("Session context cleared — answers rely on the documents alone again.");
    return;
  }
  const next = current ? `${current}\n${text}` : text;
  if (next.length > SESSION_CONTEXT_MAX) {
    toast(`Session context is limited to ${SESSION_CONTEXT_MAX} characters. ` +
      "Edit it in the Session context panel to make room.", "warn", 9000);
    return;
  }
  setSessionContext(next);
  addSystemNote(`Added to this session's context:\n${text}`);
}

function helpCommand() {
  addSystemNote([
    "Chat commands:",
    "  /report [t<id>] <query>  write a Markdown report from your knowledge base;",
    "                           t<id> formats it after an uploaded template",
    "  /revise [id] <changes>   revise a report (defaults to the last one made here)",
    "  /export [id] <format>    download a report — pdf, docx, html, latex, epub, odt, plain, md",
    "  /reports                 list the reports in this session",
    "  /templates               list your uploaded report templates",
    "  /scope [ids|all]         ask only inside chosen folders — /scope lists them,",
    "                           /scope 3,5 narrows to two, /scope all clears it",
    "  /context [facts|clear]   tell the model facts your documents don't have yet",
    "                           (a changed role, a recent event) — this session only",
    "  /help                    show this",
    "",
    "Anything without a leading / is asked against your knowledge base.",
    "",
    "To check whether something appears in only some of your folders, tick those",
    "folders in the Folders panel (or use /scope). Questions and reports are then",
    "answered from those folders and everything under them — and \"nothing found\"",
    "means nothing found there. The chip above the composer shows an active scope;",
    "✕ on it searches everything again.",
    "",
    "Adding a folder asks who may search it: this session only (other sessions",
    "can't see, search or cite it — ⤴ in the Folders panel makes it space-wide",
    "later) or every session.",
  ].join("\n"));
}
