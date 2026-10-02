/*
 * The Report button starts /reports/generate/start and polls the job; it runs
 * the same report pipeline the generate_report tool wraps — no agent turn, so
 * it works on whatever model the space is pinned to.
 *
 * The server saves the request and its outcome in the thread, like a question
 * and its answer, so a reload shows them — and a report still being written
 * when the page loads is picked up and followed to the end.
 */
import { api, ApiError, apiJson } from "../../api/client";
import type { Report, ReportJob, Template } from "../../api/types";
import { keys, queryClient } from "../../queryClient";
import { getState } from "../../store";
import { toast } from "../../ui/toast";
import { loadHistory } from "../session";
import { sessionContext } from "../sessionContext";
import { TPL_PROMPT_CHARS } from "../templates";
import { whileBusy } from "./ask";
import { addMessage, setStatus, updateMessage } from "./messages";

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

const POLL_MS = 3000;
// Polls that fail in a row before giving up — enough to ride out a proxy hiccup
// or a pod restart's first seconds, not a backend that is gone.
const MAX_POLL_FAILURES = 10;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const WRITING = "Searching the documents and writing the report…";

/** Jobs this page is polling, by id → thread. */
const following = new Map<string, number | null>();

/** The in-flight bubble for a report still being written. */
function showWriting() {
  const id = addMessage("assistant", "", true);
  setStatus(id, WRITING);
  return id;
}

/**
 * Poll a report job until it is written, then reload the thread — the server
 * has saved the reply there, so the chat shows exactly what a reload would.
 *
 * Writing takes minutes; held open as one request, the ingress's read timeout
 * cuts it off with a 504 while the server carries on and saves the report.
 * Short polls have no such limit.
 */
async function follow(jobId: string, threadId: number | null) {
  following.set(jobId, threadId);
  let failures = 0;
  try {
    for (;;) {
      await sleep(POLL_MS);
      try {
        const job = await apiJson<ReportJob>("GET", `/api/v1/reports/generate/jobs/${jobId}`);
        failures = 0;
        if (job.status === "done") return;
      } catch (e) {
        // Missing means the server restarted and lost the job; the session is gone
        // too on a 401. Neither is worth retrying.
        if (e instanceof ApiError && (e.status === 404 || e.status === 401)) {
          toast(e.status === 404
            ? "The server restarted while writing the report. The Reports list shows whether it was saved."
            : e.message, "warn", 10000);
          return;
        }
        if (++failures >= MAX_POLL_FAILURES) {
          toast(`Lost track of the report: ${e instanceof Error ? e.message : e}`, "err", 10000);
          return;
        }
      }
    }
  } finally {
    following.delete(jobId);
    // A failed run can still leave a row behind, so refresh either way.
    void queryClient.invalidateQueries({ queryKey: keys.reports(getState().spaceId) });
    if (getState().threadId === threadId) await loadHistory();
  }
}

/**
 * After the thread's history is shown: a report the server is still writing for
 * it gets its in-flight bubble back, and is followed unless already.
 */
export async function resumeReports() {
  const { threadId } = getState();
  if (threadId == null) return;
  let jobs: ReportJob[] = [];
  try {
    jobs = (await apiJson<ReportJob[]>("GET", `/api/v1/reports/generate/jobs?thread_id=${threadId}`)) ?? [];
  } catch { return; /* an older server without the listing: nothing to resume */ }
  if (getState().threadId !== threadId) return;
  for (const job of jobs) {
    showWriting();
    if (!following.has(job.job_id)) void follow(job.job_id, threadId);
  }
}

/** Write a report on `request`, formatted after `tpl` when one is given. */
export async function writeReport(request: string, tpl: Template | null) {
  if (!request || getState().busy) return;
  await whileBusy(async () => {
    const chatText = `📄 Report${tpl ? ` (template ${tpl.name})` : ""}: ${request}`;
    const { spaceId, threadId, scopeFolderIds } = getState();
    addMessage("user", chatText);
    const msgId = showWriting();
    let jobId: string;
    try {
      ({ job_id: jobId } = await apiJson<ReportJob>("POST", "/api/v1/reports/generate/start", {
        json: {
          search_space_id: spaceId,
          thread_id: threadId,
          request,
          report_style: reportStyleFor(request),
          user_instructions: buildReportInstructions(request, tpl),
          // Same scope as a question. With it set the server refuses to write a
          // report when those folders yield nothing.
          folder_ids: scopeFolderIds.length ? scopeFolderIds : undefined,
          // Saved in the thread with the outcome, so a reload keeps both.
          chat_text: chatText,
        },
      }));
    } catch (e) {
      setStatus(msgId, `Report failed: ${e instanceof Error ? e.message : e}`, "warn");
      updateMessage(msgId, { streaming: false });
      return;
    }
    await follow(jobId, threadId);
  });
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
