/*
 * /report and /revise POST to /reports/generate, which runs the same report
 * pipeline the generate_report tool wraps — no agent turn, so every command works
 * on whatever model the space is pinned to. /export and /reports use the report
 * CRUD/export routes.
 */
import { api, apiJson } from "../../api/client";
import type { GenerateReportResult, Report, Template } from "../../api/types";
import { keys, queryClient } from "../../queryClient";
import { getState, setState } from "../../store";
import { toast } from "../../ui/toast";
import { sessionContext } from "../sessionContext";
import { TPL_PROMPT_CHARS } from "../templates";
import { whileBusy } from "./ask";
import { reportHref } from "../report/route";
import { addMessage, addSystemNote, setStatus, updateMessage } from "./messages";

export const EXPORT_FORMATS = ["pdf", "docx", "html", "latex", "epub", "odt", "plain", "md"];

/**
 * The `user_instructions` argument — the only field of the generate call that
 * reaches the report-writing prompt: write in Vietnamese, and follow the template
 * when one is given.
 */
function buildReportInstructions(request: string, tpl: Template | null) {
  const base = "Viết báo cáo bằng tiếng Việt.";
  // The session note travels here rather than as session_context: the report
  // pipeline is not a chat turn, and user_instructions is its only way in.
  const note = sessionContext();
  const ask = (request.trim() ? `\n\nYêu cầu của người dùng:\n${request.trim()}` : "") +
    (note
      ? "\n\nThông tin bổ sung người dùng cung cấp cho phiên làm việc này — coi là đúng " +
        "và mới hơn tài liệu; nếu tài liệu mâu thuẫn thì tài liệu đã lỗi thời:\n" + note
      : "");
  if (!tpl) return base + ask;

  // No outline block when no headings were detected: an empty "Bố cục" heading
  // reads as "this report has no sections".
  const outlineBlock = tpl.outline.length
    ? `\n\nBố cục các phần của báo cáo mẫu:\n${tpl.outline.join("\n")}`
    : "";

  return `${base}

Báo cáo PHẢI theo đúng định dạng của báo cáo mẫu "${tpl.name}" dưới đây: giữ đúng
bố cục, thứ tự các phần và cách đặt tiêu đề; bắt chước văn phong, cách hành văn và
cách trình bày của trích đoạn mẫu. Chỉ thay nội dung cho phù hợp với chủ đề được
yêu cầu.${outlineBlock}

Trích đoạn của báo cáo mẫu (để bắt chước văn phong và cách viết):
${tpl.content.slice(0, TPL_PROMPT_CHARS)}${ask}`;
}

/** The report_style the server expects, by keyword. */
function reportStyleFor(request: string) {
  const q = request.toLowerCase();
  if (/\b(brief|ngắn|ngan|tóm tắt|tom tat|súc tích|suc tich)\b/.test(q)) return "brief";
  if (/\b(deep[\s_-]?research|nghiên cứu sâu|nghien cuu sau|chuyên sâu|chuyen sau)\b/.test(q)) return "deep_research";
  return "detailed";
}

/** Every report in the space, newest first. */
export const listReports = async () =>
  (await apiJson<Report[]>("GET", `/api/v1/reports?search_space_id=${getState().spaceId}&limit=500`)) ?? [];

/** Every report belonging to the current thread, newest first. */
export async function threadReports() {
  const { threadId } = getState();
  return (await listReports()).filter((r) => r.thread_id === threadId).sort((a, b) => b.id - a.id);
}

/** Write (or, with parentId, revise) a report. One blocking call; the bubble carries a status. */
export async function reportCommand(request: string, parentId: number | null = null) {
  if (!request) { toast("Usage: /report [t<id>] <what the report should cover>", "warn"); return; }
  if (getState().busy) return;

  // "/report t2 <query>" formats the report after template t2. A revision keeps
  // its parent's structure, so only /report takes one.
  let tpl: Template | null = null;
  const m = parentId ? null : request.match(/^t(\d+)(?:\s+([\s\S]*))?$/i);
  if (m) {
    tpl = getState().templates.find((t) => t.id === Number(m[1])) ?? null;
    if (!tpl) { toast(`No template t${m[1]}. /templates lists what you have.`, "warn"); return; }
    request = (m[2] ?? "").trim();
    if (!request) { toast(`Usage: /report t${tpl.id} <what the report should cover>`, "warn"); return; }
  }

  await whileBusy(async () => {
    addMessage("user", (parentId ? `/revise ${parentId} ` : `/report ${tpl ? `t${tpl.id} ` : ""}`) + request);
    const msgId = addMessage("assistant", "", true);
    setStatus(msgId, parentId ? "Đang sửa lại báo cáo…" : "Đang tìm tư liệu và viết báo cáo…");

    try {
      const { spaceId, threadId, scopeFolderIds } = getState();
      const res = await apiJson<GenerateReportResult>("POST", "/api/v1/reports/generate", {
        json: {
          search_space_id: spaceId,
          thread_id: threadId,
          request,
          report_style: reportStyleFor(request),
          user_instructions: buildReportInstructions(request, tpl),
          parent_report_id: parentId,
          // Same scope as a question. With it set the server refuses to write a
          // report when those folders yield nothing.
          folder_ids: scopeFolderIds.length ? scopeFolderIds : undefined,
        },
      });

      if (!res || res.status !== "ready" || !res.report_id) {
        setStatus(msgId, `Report failed: ${res?.error ?? "unknown error"}`, "warn");
        if (res?.report_id) addSystemNote(`⚠ Report #${res.report_id} failed: ${res.error ?? "unknown error"}`);
        return;
      }

      setState({ lastReportId: res.report_id });
      setStatus(msgId, null);
      updateMessage(msgId, {
        text: `Đã tạo báo cáo **“${res.title}”**${res.word_count ? ` (${res.word_count} từ)` : ""}.`,
      });
      addSystemNote(
        `📄 Report #${res.report_id} — “${res.title}” is ready.\n` +
        `Download it:  /export ${res.report_id} pdf   (also: docx, html, latex, epub, odt, plain, md)\n` +
        `Revise it:    /revise ${res.report_id} <what to change>`,
        [{ label: `Mở báo cáo #${res.report_id} để xem / sửa ↗`, href: reportHref(res.report_id) }]);
    } catch (e) {
      setStatus(msgId, `Report failed: ${e instanceof Error ? e.message : e}`, "warn");
    } finally {
      updateMessage(msgId, { streaming: false });
      // A failed run can still leave a row behind, so refresh either way.
      void queryClient.invalidateQueries({ queryKey: keys.reports(getState().spaceId) });
    }
  });
}

export function reviseCommand(arg: string) {
  const parts = arg.split(/\s+/).filter(Boolean);
  let id = getState().lastReportId;
  let instructions = arg;
  if (parts.length && /^\d+$/.test(parts[0])) {
    id = Number(parts[0]);
    instructions = parts.slice(1).join(" ");
  }
  if (!id) { toast("No report to revise. Run /report first, or /revise <report_id> <changes>.", "warn"); return; }
  if (!instructions.trim()) { toast("Usage: /revise [report_id] <what to change>", "warn"); return; }
  return reportCommand(instructions, id);
}

export async function exportCommand(arg: string) {
  const parts = arg.split(/\s+/).filter(Boolean);
  let id: string | number | null, fmt: string;
  if (parts.length >= 2) [id, fmt] = parts;
  else if (parts.length === 1) {
    if (/^\d+$/.test(parts[0])) { id = parts[0]; fmt = "pdf"; }   // "/export 12" → pdf
    else { fmt = parts[0]; id = getState().lastReportId; }       // "/export docx" → last report
  } else {
    fmt = "pdf"; id = getState().lastReportId;
  }

  if (!id) { toast("No report to export. Run /report first, or /export <report_id> <format>.", "warn"); return; }
  fmt = (fmt || "pdf").toLowerCase();
  if (fmt === "markdown") fmt = "md";
  if (!EXPORT_FORMATS.includes(fmt)) {
    toast(`Unknown format "${fmt}". One of: ${EXPORT_FORMATS.join(", ")}.`, "warn");
    return;
  }
  await downloadReport(Number(id), fmt);
}

const EXT_FOR: Record<string, string> = { latex: "tex", plain: "txt", md: "md" };
const safeFilename = (s?: string) => (s ?? "").replace(/[^\w\s-]/g, "").trim().replace(/\s+/g, "-").slice(0, 80);

/**
 * `md` is the raw Markdown source (the /content route); every other format is
 * server-rendered by /export. Content-Disposition isn't readable cross-origin, so
 * the filename is derived locally.
 */
export async function downloadReport(id: number, fmt: string) {
  try {
    if (fmt === "md") {
      const c = await apiJson<{ title?: string; content?: string }>("GET", `/api/v1/reports/${id}/content`);
      triggerDownload(new Blob([c?.content ?? ""], { type: "text/markdown;charset=utf-8" }),
        `${safeFilename(c?.title) || `report-${id}`}.md`);
    } else {
      const res = await api("GET", `/api/v1/reports/${id}/export?format=${fmt}`);
      triggerDownload(await res.blob(), `report-${id}.${EXT_FOR[fmt] ?? fmt}`);
    }
    toast(`Exported report #${id} as ${fmt}.`, "ok");
  } catch (e) {
    toast(`Export of report #${id} failed: ${e instanceof Error ? e.message : e}`, "err", 10000);
  }
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function listReportsCommand() {
  const rs = await threadReports();
  if (!rs.length) { addSystemNote("No reports in this session yet. Create one with /report <query>."); return; }
  const lines = rs.map((r) => `#${r.id} — ${r.title}${r.report_metadata?.status === "failed" ? "  (failed)" : ""}`);
  addSystemNote("Reports in this session:\n" + lines.join("\n") +
    "\n\nDownload: /export <id> <pdf|docx|html|latex|epub|odt|plain|md>",
    rs.map((r) => ({ label: `Mở #${r.id} ↗`, href: reportHref(r.id) })));
}
