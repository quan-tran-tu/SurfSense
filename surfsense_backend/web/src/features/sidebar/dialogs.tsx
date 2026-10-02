import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { FolderShare } from "../../api/types";
import { keys } from "../../queryClient";
import { useStore } from "../../store";
import { Dialog } from "../../ui/Dialog";
import { ICON } from "../../ui/icons";
import { guard } from "../../ui/toast";
import { collectFiles, copyToken, ingest, listShares, shareFolder } from "../folders";

/**
 * Where an upload is visible, asked once the folder is picked — rather than a
 * checkbox that had to be set beforehand and was easy to miss.
 */
export function UploadDialog({ files, onClose }: { files: File[]; onClose: () => void }) {
  const { root, files: indexable } = collectFiles(files);
  const go = (sessionOnly: boolean) => { onClose(); guard(() => ingest(files, sessionOnly)); };
  return (
    <Dialog title={`Add "${root}"`} onClose={onClose}>
      <p className="sub">
        {indexable.length} file{indexable.length === 1 ? "" : "s"} to index. Who should be able to search it?
      </p>
      <div className="choices">
        <button className="choice" onClick={() => go(true)} autoFocus>
          <strong>This session only</strong>
          <span className="sub">Only this chat session sees and cites it. ⤴ in Folders makes it space-wide later.</span>
        </button>
        <button className="choice" onClick={() => go(false)}>
          <strong>Every session</strong>
          <span className="sub">All your sessions search it, and it can be shared or granted to a group.</span>
        </button>
      </div>
      <div className="actions"><button className="sm" onClick={onClose}>Cancel</button></div>
    </Dialog>
  );
}

const EXPIRY = [
  { label: "1 day", days: 1 },
  { label: "7 days", days: 7 },
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
];

export const expiryText = (s: Pick<FolderShare, "expires_at" | "state">) =>
  s.state === "expired" ? `expired ${new Date(s.expires_at!).toLocaleString()}`
    : s.expires_at ? `expires ${new Date(s.expires_at).toLocaleString()}` : "never expires";

/** A token row: the token itself, its expiry, and copy. */
export function TokenLine({ s }: { s: FolderShare }) {
  return (
    <>
      <div className="stack" style={{ alignItems: "center" }}>
        <div className="token mono grow">{s.token}</div>
        <button className="sm" title="Copy the token" disabled={s.state !== "live"}
          onClick={() => copyToken(s.token)}>{ICON.copy}</button>
      </div>
      <div className={`note${s.state !== "live" ? " warn" : ""}`} style={{ marginTop: 0 }}>{expiryText(s)}</div>
    </>
  );
}

/**
 * Share a folder. A folder has one working token at a time: once it has one,
 * the dialog shows it instead of offering another. A new one is minted only
 * after that one is revoked or expires.
 */
export function ShareDialog({ folderId, path, onClose }: { folderId: number; path: string; onClose: () => void }) {
  const spaceId = useStore((s) => s.spaceId);
  const shares = useQuery({ queryKey: keys.shares(spaceId), queryFn: listShares });
  const live = (shares.data ?? []).filter((s) => s.source_folder_id === folderId && s.state === "live");
  const [days, setDays] = useState(7);
  const [minted, setMinted] = useState<string | null>(null);
  const share = () => guard(async () => {
    setMinted(await shareFolder(path, new Date(Date.now() + days * 86_400_000).toISOString()));
  });
  return (
    <Dialog title={`Share "${path}"`} onClose={onClose}>
      <p className="sub">
        Anyone you hand a token to can import this folder, read-only, until it expires or you
        revoke it. Tokens are listed under "Shared by you" until you revoke them.
      </p>
      {live.length > 0 && <>
        <label>{live.length === 1 ? "Its token" : "Its tokens"}</label>
        {live.map((s) => <TokenLine key={s.id} s={s} />)}
      </>}
      {minted && !live.some((s) => s.token === minted) && (
        <div className="token mono">{minted}</div>
      )}
      {!live.length && !minted && <>
        <label htmlFor="shareExpiry">Expires after</label>
        <select id="shareExpiry" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {EXPIRY.map((o) => <option key={o.days} value={o.days}>{o.label}</option>)}
        </select>
      </>}
      <div className="actions">
        {!live.length && !minted && <button className="sm primary" onClick={share}>Create token</button>}
        <button className="sm" onClick={onClose}>Close</button>
      </div>
    </Dialog>
  );
}
