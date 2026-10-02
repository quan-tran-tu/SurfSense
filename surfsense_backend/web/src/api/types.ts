/** Wire shapes of the backend routes this client calls. */

/** A share ends when revoked or when its expiry passes; both are final. */
export type ShareState = "live" | "revoked" | "expired";

/** GET /search-spaces/{id}/folder-shares — the tokens you minted and haven't revoked. */
export interface FolderShare {
  id: number;
  token: string;
  source_folder_id: number;
  folder_name: string | null;
  created_at: string;
  expires_at: string | null;
  state: ShareState;
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
  promoted_from_thread_id: number | null;   // set on a root ⤴ promoted out of that session
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
  state: ShareState;
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

/** A report being written in the background; `result` lands once it is done. */
export interface ReportJob {
  job_id: string;
  status: "running" | "done";
  result: GenerateReportResult | null;
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
  promoted_from_thread_id: number | null;
}

/** A space-wide root folder as the admin's Folder access tab lists it. */
export interface AdminRootFolder {
  id: number;
  name: string;
  owner_email: string | null;
  document_count: number;
  group_ids: number[];
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
  state: ShareState;
}

/** One importer of a share token — kept after their import is gone. */
export interface AdminShareImport {
  id: number;
  share_id: number;
  user_email: string | null;
  first_imported_at: string;
  last_imported_at: string;
  import_count: number;
  /** using, or how it stopped: removed | removed_by_admin | revoked | expired. */
  status: string;
  stopped_at: string | null;
  /** The current link while there is one. */
  link_id: number | null;
}

/** Days per folder kind; null keeps that kind forever. */
export interface RetentionDays {
  session: number | null;
  group: number | null;
  admin: number | null;
  space: number | null;
}

export interface RetentionSettings {
  effective: RetentionDays;
  deployment: RetentionDays;
  overridden: boolean;
}
