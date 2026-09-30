import { markdown } from "@codemirror/lang-markdown";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { basicSetup } from "codemirror";
import { useEffect, useRef } from "react";

/** The page's palette, so the editor reads as part of it rather than a widget. */
const theme = EditorView.theme({
  "&": { height: "100%", fontSize: "13.5px", backgroundColor: "var(--bg)", color: "var(--fg)" },
  ".cm-scroller": { fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace', lineHeight: "1.6" },
  ".cm-content": { caretColor: "var(--fg)", padding: "12px 0" },
  ".cm-gutters": { backgroundColor: "var(--panel)", color: "var(--muted)", border: "none" },
  ".cm-activeLine": { backgroundColor: "rgba(255,255,255,.03)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--panel-2)" },
  "&.cm-focused": { outline: "none" },
  "&.cm-focused .cm-cursor": { borderLeftColor: "var(--fg)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
    backgroundColor: "rgba(76, 141, 255, .30) !important",
  },
  ".cm-panels": { backgroundColor: "var(--panel)", color: "var(--fg)" },
  ".cm-searchMatch": { backgroundColor: "rgba(210, 153, 34, .35)" },
}, { dark: true });

const highlight = HighlightStyle.define([
  { tag: tags.heading, color: "#79b8ff", fontWeight: "600" },
  { tag: tags.strong, fontWeight: "700", color: "#e6e8ee" },
  { tag: tags.emphasis, fontStyle: "italic" },
  { tag: [tags.link, tags.url], color: "#4c8dff" },
  { tag: tags.monospace, color: "#d2a8ff" },
  { tag: [tags.processingInstruction, tags.contentSeparator, tags.meta], color: "#8b93a7" },
  { tag: tags.list, color: "#d29922" },
  { tag: tags.quote, color: "#8b93a7", fontStyle: "italic" },
]);

interface Props {
  value: string;
  onChange: (text: string) => void;
  onSave: () => void;
  onScroll?: (fraction: number) => void;
}

/**
 * Markdown source editor (CodeMirror 6): line numbers, undo history, search,
 * syntax colouring. Uncontrolled inside — it owns the document while typing —
 * and only takes `value` from outside when it differs, i.e. when another
 * version or a freshly loaded report is swapped in.
 */
export default function MarkdownEditor({ value, onChange, onSave, onScroll }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  // The latest callbacks, so the editor built once always calls the current ones.
  const cb = useRef({ onChange, onSave, onScroll });
  cb.current = { onChange, onSave, onScroll };

  useEffect(() => {
    const v = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: value,
        extensions: [
          keymap.of([{ key: "Mod-s", preventDefault: true, run: () => { cb.current.onSave(); return true; } }]),
          basicSetup,
          markdown(),
          EditorView.lineWrapping,
          theme,
          syntaxHighlighting(highlight),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) cb.current.onChange(u.state.doc.toString());
          }),
          EditorView.domEventHandlers({
            scroll: (_e, ed) => {
              const s = ed.scrollDOM;
              const max = s.scrollHeight - s.clientHeight;
              cb.current.onScroll?.(max > 0 ? s.scrollTop / max : 0);
            },
          }),
        ],
      }),
    });
    view.current = v;
    v.focus();
    return () => { v.destroy(); view.current = null; };
    // Built once; later values arrive through the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) {
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
    }
  }, [value]);

  return <div className="md-editor" ref={host} />;
}
