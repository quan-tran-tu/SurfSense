/**
 * One glyph per action, used by every button that does it — sidebar and admin
 * page alike — so the same action always looks the same. A button showing only
 * a glyph carries a `title` saying what it does.
 */
export const ICON = {
  remove: "✕",    // delete, remove, revoke: anything that takes something away
  promote: "⤴",   // session-only folder → every session
  demote: "⤵",    // undo a promotion: back to the session it came from
  edit: "✎",
  copy: "⧉",
} as const;
