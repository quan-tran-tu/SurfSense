import { QueryClient } from "@tanstack/react-query";

/**
 * One client, importable from plain modules too: an action or an upload that
 * changes server state invalidates the queries that show it, and every view
 * reading them repaints — nobody has to remember which render function to call.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
  },
});

export const keys = {
  threads: (spaceId: number | null) => ["threads", spaceId] as const,
  tree: (spaceId: number | null) => ["tree", spaceId] as const,
  folderDocs: (spaceId: number | null, folderId: number) => ["folderDocs", spaceId, folderId] as const,
  imports: (spaceId: number | null) => ["imports", spaceId] as const,
  shares: (spaceId: number | null) => ["shares", spaceId] as const,
  reports: (spaceId: number | null) => ["reports", spaceId] as const,
  admin: ["admin"] as const,
};

/**
 * Who can read which folder changes in other browsers — an owner revokes a
 * token, an admin revokes it or grants a folder to a group, someone imports —
 * so the lists showing it poll rather than wait for a write made here. Polling
 * pauses while the tab is hidden and catches up the moment it is shown again.
 */
const LIVE_MS = 5_000;
for (const queryKey of [["tree"], ["imports"], ["shares"], ["admin", "folders"], ["admin", "shares"], ["admin", "links"]]) {
  queryClient.setQueryDefaults(queryKey, { refetchInterval: LIVE_MS, refetchOnWindowFocus: true });
}

/** Everything the folder panels show: the tree, its files, imports and shares. */
export async function refreshFolders() {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ["tree"] }),
    queryClient.invalidateQueries({ queryKey: ["folderDocs"] }),
    queryClient.invalidateQueries({ queryKey: ["imports"] }),
    queryClient.invalidateQueries({ queryKey: ["shares"] }),
  ]);
}
