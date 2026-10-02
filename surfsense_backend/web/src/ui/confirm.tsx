/*
 * The one confirmation every edit and delete goes through, in the app's own
 * dialog rather than the browser's confirm(): it names the action on its button
 * and marks the destructive ones red, so "OK" never has to be guessed at.
 */
import { create } from "zustand";
import { Dialog } from "./Dialog";
import { guard } from "./toast";

export interface ConfirmOpts {
  title: string;
  message?: string;
  confirmLabel?: string;   // the verb on the button: "Delete", "Revoke", "Save"…
  danger?: boolean;        // red button, for what can't be undone
}

interface Pending extends ConfirmOpts { resolve: (ok: boolean) => void }

const usePending = create<{ pending: Pending | null }>(() => ({ pending: null }));

/** Ask, and resolve to whether the user confirmed. */
export function confirmAction(opts: ConfirmOpts): Promise<boolean> {
  return new Promise((resolve) => {
    // A second ask while one is open answers the first with "no".
    usePending.getState().pending?.resolve(false);
    usePending.setState({ pending: { ...opts, resolve } });
  });
}

/** Ask, then run `fn` under guard() if confirmed. */
export async function confirmThen(opts: ConfirmOpts, fn: () => unknown | Promise<unknown>) {
  if (await confirmAction(opts)) await guard(fn);
}

export function ConfirmHost() {
  const pending = usePending((s) => s.pending);
  if (!pending) return null;
  const answer = (ok: boolean) => { usePending.setState({ pending: null }); pending.resolve(ok); };
  return (
    <Dialog title={pending.title} onClose={() => answer(false)}>
      {pending.message && <p className="sub" style={{ whiteSpace: "pre-line" }}>{pending.message}</p>}
      <div className="actions">
        <button className={`sm ${pending.danger ? "danger solid" : "primary"}`} autoFocus onClick={() => answer(true)}>
          {pending.confirmLabel ?? "Confirm"}
        </button>
        <button className="sm" onClick={() => answer(false)}>Cancel</button>
      </div>
    </Dialog>
  );
}
