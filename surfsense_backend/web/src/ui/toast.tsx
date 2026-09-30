import { create } from "zustand";

export type ToastKind = "ok" | "warn" | "err";
interface Toast { id: number; msg: string; kind: ToastKind }

const useToasts = create<{ toasts: Toast[] }>(() => ({ toasts: [] }));
let nextId = 1;

export function toast(msg: string, kind: ToastKind = "ok", ms = 6000) {
  const id = nextId++;
  useToasts.setState((s) => ({ toasts: [...s.toasts, { id, msg, kind }] }));
  setTimeout(() => useToasts.setState((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), ms);
}

/** Runs an async handler, turning any error into a toast instead of a dead UI. */
export async function guard(fn: () => unknown | Promise<unknown>) {
  try { await fn(); }
  catch (e) { toast(e instanceof Error ? e.message : String(e), "err", 10000); }
}

export function Toasts() {
  const toasts = useToasts((s) => s.toasts);
  return (
    <div id="toasts">
      {toasts.map((t) => <div key={t.id} className={`toast ${t.kind}`}>{t.msg}</div>)}
    </div>
  );
}
