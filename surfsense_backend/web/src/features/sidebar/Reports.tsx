import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { Report } from "../../api/types";
import { keys } from "../../queryClient";
import { useStore } from "../../store";
import { listReports } from "../chat/reports";
import { reportHref } from "../report/route";

interface ReportGroup {
  latest: Report;
  versions: number;
}

/**
 * One row per report, not per version: /revise adds versions to a group, and the
 * row stands for the group, opening its newest version (the canvas switches
 * between the rest).
 */
function groupReports(reports: Report[]): ReportGroup[] {
  const groups = new Map<number, ReportGroup>();
  for (const r of reports) {
    const key = r.report_group_id ?? r.id;
    const g = groups.get(key);
    if (!g) groups.set(key, { latest: r, versions: 1 });
    else {
      g.versions += 1;
      if (r.id > g.latest.id) g.latest = r;
    }
  }
  return [...groups.values()].sort((a, b) => b.latest.id - a.latest.id);
}

/**
 * The reports written in this space, so opening one never needs /reports. The
 * list follows the open session by default; "all" shows every session's.
 */
export function ReportList() {
  const spaceId = useStore((s) => s.spaceId);
  const threadId = useStore((s) => s.threadId);
  const [scope, setScope] = useState<"session" | "all">("session");
  const q = useQuery({ queryKey: keys.reports(spaceId), queryFn: listReports });

  const mine = (q.data ?? []).filter((r) => scope === "all" || r.thread_id === threadId);
  const groups = groupReports(mine);

  return (
    <>
      <div className="seg small">
        <button className={scope === "session" ? "on" : ""} onClick={() => setScope("session")}>Session này</button>
        <button className={scope === "all" ? "on" : ""} onClick={() => setScope("all")}>Tất cả</button>
      </div>
      {q.isPending && <div className="empty">loading…</div>}
      {q.data && !groups.length && (
        <div className="empty">{scope === "session" ? "chưa có báo cáo trong session này" : "chưa có báo cáo"}</div>
      )}
      {groups.map(({ latest: r, versions }) => {
        const failed = r.report_metadata?.status === "failed";
        return (
          <a key={r.id} className="item report" href={reportHref(r.id)} target="_blank" rel="noopener"
            title={`#${r.id} · ${new Date(r.created_at).toLocaleString()} — mở trong tab mới`}>
            <span className="name">{r.title}</span>
            {versions > 1 && <span className="badge">v{versions}</span>}
            {failed && <span className="badge dead">lỗi</span>}
          </a>
        );
      })}
    </>
  );
}
