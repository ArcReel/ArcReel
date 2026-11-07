// Read models for the file view (backend lib/project/file_view.py and lib/project/reference_index.py).

/** Role of a file in the project, determined by references; infrastructure files are not returned. */
export type FileViewRole =
  | "structure"
  | "referenced"
  | "history"
  | "unreferenced"
  | "derived"
  | "agent_config"
  | "memory";

/** UI object owning the file, used by "jump to owner". */
export interface FileViewOwner {
  kind: "asset" | "segment" | "reference_unit" | "grid" | "episode" | "timeline" | "source" | "memory" | "project";
  id?: string;
  episode?: number;
  asset_type?: "character" | "scene" | "prop" | "product";
}

export interface FileViewEntry {
  name: string;
  /** Project-relative POSIX path. */
  path: string;
  type: "file" | "directory";
  /** Size in bytes; for a directory, the sum of its visible files. */
  size: number;
  /** UTC ISO 8601 with a timezone offset. */
  modified_at: string;
  /** Only files carry a role. */
  role?: FileViewRole;
  owner?: FileViewOwner | null;
  /** Present only on the current-version snapshot in history and always true; the server refuses its deletion. */
  is_current?: true;
  /** Present and always true only for a remote content version work file whose local cache has been cleared: the server retrieves it from object storage before preview, download, or deletion. */
  remote_only?: true;
}

export interface FileViewListing {
  path: string;
  entries: FileViewEntry[];
}

// Project trash (backend lib/project/project_trash.py).

/** One deletion record. `object.kind` is the deleted object type; an unreferenced file deleted through the file view uses `file`. */
export interface TrashRecord {
  id: string;
  object: { kind: string; path?: string; [key: string]: unknown };
  /** Project-relative paths of deleted files before deletion. */
  files: string[];
  schema_version: number;
  /** UTC ISO 8601 with a timezone offset. */
  deleted_at: string;
  origin: "user" | "agent";
  size: number;
  has_snapshot: boolean;
  /** A record that could not be restored in its original structure and has already undergone degraded restoration: only structure files remain, available for download only. */
  download_only: boolean;
}

export interface TrashListing {
  records: TrashRecord[];
  total_size: number;
  retention_days: number;
}

// Deleted projects (backend lib/project/deleted_projects.py).

/** One deleted project. `id` is its tombstone name, used for restoration and permanent deletion; `name` is the project name before deletion. */
export interface DeletedProject {
  id: string;
  name: string;
  title: string;
  /** UTC ISO 8601 with a timezone offset. */
  deleted_at: string;
  /** Automatic expiration cleanup time, also UTC ISO 8601. */
  expires_at: string;
  /** Bytes occupied by the project directory. */
  size: number;
}

export interface DeletedProjectListing {
  projects: DeletedProject[];
  retention_days: number;
}

/** A restored file's destination: when `as_history` is true, it becomes a historical version of its resource. */
export interface RestoredFile {
  original: string;
  path: string;
  as_history: boolean;
}

/** Result of one restoration. `object` describes the restored object and differs from the record when renamed. */
export interface RestoreResult {
  object: TrashRecord["object"];
  restored: RestoredFile[];
  /** When true, the object could not be restored in its original structure, and only its files were restored. */
  degraded: boolean;
  /** Structure files left in the record, available for download only. */
  download_only: string[];
}
