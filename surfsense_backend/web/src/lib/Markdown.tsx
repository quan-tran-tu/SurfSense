import { memo, type AnchorHTMLAttributes } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import { setState, useStore } from "../store";

const CHUNK = "#cite:";
const URL_CITE = "#citeurl:";

/**
 * The backend emits sources as [citation:<payload>], where the payload is a chunk
 * id (shared/citations/markers.py) or a URL. Rewrite each into an ordinary link
 * the renderer below turns into a numbered chip. Numbering restarts at 1 per
 * message and stays stable as an answer streams in.
 */
function linkCitations(src: string): string {
  const ordinals = new Map<string, number>();
  return src
    // A model writing a range as two brackets — "[58]-[60]" — leaves its hyphen
    // between the chips. Drop it when tight against both; a spaced " - " is prose.
    .replace(/(\[citation:[^\]]+\])[-–—](?=\[citation:)/g, "$1")
    .replace(/\[citation:([^\]]+)\]/g, (_, payload: string) => {
      const key = payload.trim();
      if (!ordinals.has(key)) ordinals.set(key, ordinals.size + 1);
      const n = ordinals.get(key);
      const target = /^https?:/i.test(key) ? URL_CITE : CHUNK;
      return `[${n}](<${target}${encodeURIComponent(key)}>)`;
    });
}

function Anchor({ msgId, href = "", children, ...rest }:
  AnchorHTMLAttributes<HTMLAnchorElement> & { msgId: number }) {
  const active = useStore((s) => s.source?.kind === "chunk" && s.source.key === `${msgId}:${href}`);
  if (href.startsWith(URL_CITE)) {
    const url = decodeURIComponent(href.slice(URL_CITE.length));
    return <a className="cite" href={url} target="_blank" rel="noopener" title={url}>{children}</a>;
  }
  if (href.startsWith(CHUNK)) {
    const chunkId = decodeURIComponent(href.slice(CHUNK.length));
    const ordinal = String(children);
    return (
      <a className={`cite${active ? " on" : ""}`} title={`Show source ${ordinal}`}
        onClick={(e) => {
          e.preventDefault();
          setState({ source: { kind: "chunk", chunkId, ordinal, key: `${msgId}:${href}` } });
        }}>
        {children}
      </a>
    );
  }
  return <a {...rest} href={href} target="_blank" rel="noopener">{children}</a>;
}

/**
 * An answer or report as Markdown (GFM: tables included). Raw HTML in the source
 * is shown as text, never rendered, so a model that emits <script> can't run it.
 */
export const Markdown = memo(function Markdown({ text, msgId = 0 }: { text: string; msgId?: number }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      urlTransform={(url) => (url.startsWith("#cite") ? url : defaultUrlTransform(url))}
      components={{ a: ({ node: _node, ...props }) => <Anchor msgId={msgId} {...props} /> }}
    >
      {linkCitations(text)}
    </ReactMarkdown>
  );
});
