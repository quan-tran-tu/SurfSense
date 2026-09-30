import { create } from "zustand";

/**
 * Layout preferences — sidebar width, collapsed sections. Per browser, not per
 * user, and only a convenience: storage that throws or comes back empty just
 * means the defaults.
 */
const LS = "osint.layout";

export const SIDEBAR_MIN = 220;
export const SIDEBAR_MAX = 640;
const SIDEBAR_DEFAULT = 300;

interface Layout {
  sidebarWidth: number;
  collapsed: Record<string, boolean>;
}

function read(): Layout {
  try {
    const v = JSON.parse(localStorage.getItem(LS) || "{}");
    return {
      sidebarWidth: clampWidth(Number(v.sidebarWidth) || SIDEBAR_DEFAULT),
      collapsed: v.collapsed && typeof v.collapsed === "object" ? v.collapsed : {},
    };
  } catch {
    return { sidebarWidth: SIDEBAR_DEFAULT, collapsed: {} };
  }
}

export const clampWidth = (w: number) => Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, Math.round(w)));

export const useLayout = create<Layout>(read);

useLayout.subscribe((s) => {
  try { localStorage.setItem(LS, JSON.stringify(s)); } catch { /* defaults next time */ }
});

export const setSidebarWidth = (w: number) => useLayout.setState({ sidebarWidth: clampWidth(w) });

export const toggleSection = (id: string) =>
  useLayout.setState((s) => ({ collapsed: { ...s.collapsed, [id]: !s.collapsed[id] } }));
