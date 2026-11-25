
export type FileViewRole =
  | "structure"
  | "referenced"
  | "history"
  | "unreferenced"
  | "derived"
  | "agent_config"
  | "memory";

export interface FileViewOwner {
  kind: "asset" | "segment" | "reference_unit" | "grid" | "episode" | "timeline" | "source" | "memory" | "project";
  id?: string;
  episode?: number;
  asset_type?: "character" | "scene" | "prop" | "product";
}

export interface FileViewEntry {
  name: string;
  path: string;
  type: "file" | "directory";
  size: number;
  modified_at: string;
  role?: FileViewRole;
  owner?: FileViewOwner | null;
  is_current?: true;
  remote_only?: true;
}

export interface FileViewListing {
  path: string;
  entries: FileViewEntry[];
}


export interface TrashRecord {
  id: string;
  object: { kind: string; path?: string; [key: string]: unknown };
  files: string[];
  schema_version: number;
  deleted_at: string;
  origin: "user" | "agent" | "migration";
  size: number;
  has_snapshot: boolean;
  download_only: boolean;
}

export interface TrashListing {
  records: TrashRecord[];
  total_size: number;
  retention_days: number;
}


export interface DeletedProject {
  id: string;
  name: string;
  title: string;
  deleted_at: string;
  expires_at: string;
  size: number;
}

export interface DeletedProjectListing {
  projects: DeletedProject[];
  retention_days: number;
}

export interface RestoredFile {
  original: string;
  path: string;
  as_history: boolean;
}

export interface RestoreResult {
  object: TrashRecord["object"];
  restored: RestoredFile[];
  degraded: boolean;
  download_only: string[];
}
