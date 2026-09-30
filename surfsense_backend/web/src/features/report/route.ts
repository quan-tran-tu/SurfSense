/*
 * The report canvas lives at "#/report/<id>" and opens in its own browser tab,
 * so the chat keeps running beside it. The same bundle serves both: at boot the
 * page looks at the hash and shows either the chat app or one report.
 */

const ROUTE = /^#\/report\/(\d+)$/;

export const reportIdFromHash = (hash = location.hash): number | null => {
  const m = hash.match(ROUTE);
  return m ? Number(m[1]) : null;
};

export const reportHref = (id: number) => `#/report/${id}`;

/** Open a report's canvas in a new tab. */
export const openReportTab = (id: number) => window.open(reportHref(id), "_blank", "noopener");
