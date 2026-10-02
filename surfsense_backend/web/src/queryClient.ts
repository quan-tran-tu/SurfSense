import { QueryClient } from "@tanstack/react-query";

/**
 * One client, importable from plain modules too: a command or an upload that
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

/** Everything the folder panels show: the tree, its files, imports and shares. */
export async function refreshFolders() {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ["tree"] }),
    queryClient.invalidateQueries({ queryKey: ["folderDocs"] }),
    queryClient.invalidateQueries({ queryKey: ["imports"] }),
    queryClient.invalidateQueries({ queryKey: ["shares"] }),
  ]);
}
