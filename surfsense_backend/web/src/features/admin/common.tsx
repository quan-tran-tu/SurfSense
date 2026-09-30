import { useQuery } from "@tanstack/react-query";
import { adminApi } from "../../api/client";
import type { AdminGroup, AdminUser } from "../../api/types";
import { queryClient } from "../../queryClient";

export const adminKeys = {
  users: ["admin", "users"] as const,
  userFolders: (id: string) => ["admin", "userFolders", id] as const,
  groups: ["admin", "groups"] as const,
  groupMembers: (id: number) => ["admin", "group", id, "members"] as const,
  groupFolders: (id: number) => ["admin", "group", id, "folders"] as const,
  shares: ["admin", "shares"] as const,
  links: ["admin", "links"] as const,
};

/** Everything on the admin page is refetched after any admin write. */
export const refreshAdmin = () => queryClient.invalidateQueries({ queryKey: ["admin"] });

export const useAdminUsers = () =>
  useQuery({ queryKey: adminKeys.users, queryFn: () => adminApi<AdminUser[]>("GET", "/users") });

export const useAdminGroups = () =>
  useQuery({ queryKey: adminKeys.groups, queryFn: () => adminApi<AdminGroup[]>("GET", "/groups") });

export const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "never");

export const Badge = ({ text, cls = "" }: { text: string; cls?: string }) =>
  <span className={`badge ${cls}`.trim()}>{text}</span>;
