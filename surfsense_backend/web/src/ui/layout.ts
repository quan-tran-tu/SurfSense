import { create } from "zustand";

/**
 * Layout preferences — sidebar width, collapsed sections. Per browser, not per
 * user, and only a convenience: storage that throws or comes back empty just
 * means the defaults.
 */
const LS = "osint.layout";

export const SIDEBAR_MIN = 220;
export const SIDEBAR_MAX = 640;
// Wide enough for a share token on one line.
export const SIDEBAR_DEFAULT = 380;

interface Layout {
  sidebarWidth: number;
  // Only a width the user dragged to is kept; otherwise the default applies,
  // so raising the default reaches browsers that saved the old one.
  widthSet?: boolean;
  sidebarHidden: boolean;
  collapsed: Record<string, boolean>;
}

function read(): Layout {
  try {
    const v = JSON.parse(localStorage.getItem(LS) || "{}");
    return {
      sidebarWidth: v.widthSet ? clampWidth(Number(v.sidebarWidth) || SIDEBAR_DEFAULT) : SIDEBAR_DEFAULT,
      widthSet: v.widthSet === true,
      sidebarHidden: v.sidebarHidden === true,
      collapsed: v.collapsed && typeof v.collapsed === "object" ? v.collapsed : {},
    };
  } catch {
    return { sidebarWidth: SIDEBAR_DEFAULT, sidebarHidden: false, collapsed: {} };
  }
}

export const clampWidth = (w: number) => Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, Math.round(w)));

export const useLayout = create<Layout>(read);

useLayout.subscribe((s) => {
  try { localStorage.setItem(LS, JSON.stringify(s)); } catch { /* defaults next time */ }
});

export const toggleSidebar = () => useLayout.setState((s) => ({ sidebarHidden: !s.sidebarHidden }));

export const setSidebarWidth = (w: number) => useLayout.setState({ sidebarWidth: clampWidth(w), widthSet: true });

/** Whether a section is folded; `byDefault` holds until the user toggles it. */
export const useCollapsed = (id: string, byDefault = false) =>
  useLayout((s) => s.collapsed[id] ?? byDefault);

export const toggleSection = (id: string, byDefault = false) =>
  useLayout.setState((s) => ({ collapsed: { ...s.collapsed, [id]: !(s.collapsed[id] ?? byDefault) } }));
