/** Wire shapes of the backend routes this client calls. */

export interface Share {
  path: string;
  token: string;
  expiresAt?: string | null;   // ISO; null/absent = never (shares minted before expiry existed)
}

export interface Template {
  id: number;
  name: string;
  outline: string[];
  content: string;
}

export interface Thread { id: number; title: string }

export type FolderOrigin = "own" | "linked" | "user" | "group";

/** GET /search-spaces/{id}/folder-tree (app/routes/folders_routes.py FolderTreeNode). */
export interface FolderNode {
  id: number;
  name: string;
  parent_id: number | null;
  owner_thread_id: number | null;
  origin: FolderOrigin;
  owner_email: string | null;
  group_name: string | null;
  document_count: number;
}

export interface DocStatus { state?: string }

export interface FolderDocument {
  id: number;
  title: string;
  document_type: string;
  status: DocStatus | null;
}

export interface FolderLink {
  id: number;
  share_id: number;
  source_folder_id: number;
  folder_name: string;
  live: boolean;
}

export interface WatchedFolder { id: number; name: string; owner_thread_id: number | null }

export interface Report {
  id: number;
  title: string;
  thread_id: number | null;
  report_group_id: number | null;
  created_at: string;
  report_metadata?: { status?: string } | null;
}

export interface GenerateReportResult {
  status: string;
  report_id: number | null;
  title?: string;
  word_count?: number;
  error?: string;
}

export interface Chunk { id: number; content: string }

/* -------------------------------------------------------------------- admin */

export interface AdminUser {
  id: string;
  email: string;
  display_name: string | null;
  is_active: boolean;
  is_superuser: boolean;
  last_login: string | null;
  search_space_count: number;
  folder_count: number;
  document_count: number;
}

export interface AdminFolder {
  id: number;
  name: string;
  parent_id: number | null;
  owner_thread_id: number | null;
}

export interface AdminGroup {
  id: number;
  name: string;
  description: string | null;
  member_count: number;
  folder_count: number;
}

export interface AdminGroupMember {
  user_id: string;
  email: string;
  display_name: string | null;
  is_superuser: boolean;
}

export interface AdminGroupFolder {
  folder_id: number;
  name: string;
  owner_email: string | null;
  document_count: number;
}

export interface AdminShare {
  id: number;
  name: string | null;
  source_folder_id: number;
  source_folder_name: string | null;
  created_by_email: string | null;
  created_at: string;
  expires_at: string | null;
  uses_count: number;
  link_count: number;
  revoked_at: string | null;
}

export interface AdminLink {
  id: number;
  share_id: number;
  source_folder_id: number;
  source_folder_name: string | null;
  target_search_space_id: number;
  target_owner_email: string | null;
}
