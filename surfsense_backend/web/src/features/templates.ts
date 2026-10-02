/*
 * Report templates are exemplar reports the user uploads; /report t<id> makes
 * the report follow their structure and style. They are per-user browser state
 * (like session notes): the backend has no template storage, and the format reaches the
 * model inside the report instructions.
 *
 * .docx is the preferred format — real Vietnamese reports live in Word — and the
 * browser can't read it, so anything that isn't plain text is converted
 * server-side by POST /documents/extract-markdown (docling). .md/.txt are read
 * locally. Only size is checked before upload: what docling can read is its call.
 */
import { apiJson } from "../api/client";
import type { Template } from "../api/types";
import { getState, setPersisted } from "../store";
import { toast } from "../ui/toast";

const TPL_TEXT_EXT = /\.(md|markdown|txt)$/i;   // readable in the browser
// Requiring a leading letter keeps "Báo cáo v1.2" from losing ".2".
const TPL_NAME_EXT = /\.[a-z][a-z0-9]{0,7}$/i;
const TPL_MAX_BYTES = 20 * 1024 * 1024;  // matches the backend conversion cap
const TPL_STORE_CHARS = 20000;           // how much of the template body we keep
export const TPL_PROMPT_CHARS = 4000;    // style excerpt embedded in the instructions
const TPL_MAX_SECTIONS = 40;

/**
 * Pull the section skeleton out of a template. Vietnamese reports rarely use
 * Markdown headings, so beyond `#` we accept the forms they actually use:
 * "PHẦN I:", "Chương 2.", "I.", "1.2)", and short ALL-CAPS lines (checked with
 * the vi locale so "MỞ ĐẦU" counts).
 */
export function extractOutline(text: string): string[] {
  const outline: string[] = [];
  let inFence = false;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (line.startsWith("```")) { inFence = !inFence; continue; }
    if (inFence || !line || line.length > 120) continue;

    const isMd = /^#{1,6}\s+\S/.test(line);
    const isNumbered =
      /^(?:PHẦN|CHƯƠNG|MỤC|Phần|Chương|Mục)?\s*(?:[IVXLC]{1,7}|\d+(?:\.\d+){0,3})\s*[.):–-]\s+\S/.test(line);
    const letters = (line.match(/\p{L}/gu) || []).length;
    const isCaps = letters >= 3 && line.length <= 80 &&
      line === line.toLocaleUpperCase("vi") && line !== line.toLocaleLowerCase("vi");

    if (isMd || isNumbered || isCaps) outline.push(line);
    if (outline.length >= TPL_MAX_SECTIONS) break;
  }
  return outline;
}

/** The template's text: read locally when the browser can, else convert server-side. */
async function templateText(f: File): Promise<string> {
  if (TPL_TEXT_EXT.test(f.name)) return (await f.text()).trim();
  toast(`Converting "${f.name}" on the server…`, "ok", 5000);
  const fd = new FormData();
  fd.append("file", f, f.name);
  const res = await apiJson<{ markdown?: string }>("POST", "/api/v1/documents/extract-markdown", { body: fd });
  return (res?.markdown ?? "").trim();
}

export async function addTemplates(files: File[]) {
  for (const f of files) {
    if (f.size > TPL_MAX_BYTES) {
      toast(`"${f.name}" is over ${Math.round(TPL_MAX_BYTES / (1024 * 1024))}MB — too large for a template.`, "warn");
      continue;
    }
    let text: string;
    try { text = await templateText(f); }
    catch (e) {
      toast(`Could not convert "${f.name}": ${e instanceof Error ? e.message : e}`, "err", 12000);
      continue;
    }
    if (!text) {
      toast(`"${f.name}" converted to an empty document — nothing to use as a template.`, "warn", 10000);
      continue;
    }

    const outline = extractOutline(text);
    const templates = getState().templates;
    const id = templates.reduce((m, t) => Math.max(m, t.id), 0) + 1;
    const tpl: Template = { id, name: f.name.replace(TPL_NAME_EXT, ""), outline, content: text.slice(0, TPL_STORE_CHARS) };
    setPersisted({ templates: [...templates, tpl] });
    const shape = outline.length ? `${outline.length} sections` : "no headings detected — style only";
    toast(`Template t${id} — "${f.name}" added (${shape}). Use it: /report t${id} <query>`, "ok", 9000);
  }
}

export function removeTemplate(id: number) {
  setPersisted({ templates: getState().templates.filter((t) => t.id !== id) });
  toast(`Removed template t${id}.`);
}
