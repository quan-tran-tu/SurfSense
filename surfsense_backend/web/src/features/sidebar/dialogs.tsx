import { useState } from "react";
import { Dialog } from "../../ui/Dialog";
import { guard } from "../../ui/toast";
import { collectFiles, ingest, shareFolder } from "../folders";

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
  { label: "Never", days: 0 },
];

/** Mint a share token, with how long it lets people import the folder. */
export function ShareDialog({ path, onClose }: { path: string; onClose: () => void }) {
  const [days, setDays] = useState(7);
  const share = () => {
    const expiresAt = days ? new Date(Date.now() + days * 86_400_000).toISOString() : null;
    onClose();
    guard(() => shareFolder(path, expiresAt));
  };
  return (
    <Dialog title={`Share "${path}"`} onClose={onClose}>
      <p className="sub">
        Anyone you hand the token to can import this folder, read-only, until it expires or you
        revoke it. Once it expires, their imports stop answering.
      </p>
      <label htmlFor="shareExpiry">Expires after</label>
      <select id="shareExpiry" value={days} onChange={(e) => setDays(Number(e.target.value))}>
        {EXPIRY.map((o) => <option key={o.days} value={o.days}>{o.label}</option>)}
      </select>
      <div className="actions">
        <button className="primary sm" onClick={share}>Create token</button>
        <button className="sm" onClick={onClose}>Cancel</button>
      </div>
    </Dialog>
  );
}
