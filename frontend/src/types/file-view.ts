// 文件视图的读模型（后端 lib/project/file_view.py 与 lib/project/reference_index.py）。

/** 文件在项目中的角色，由引用关系判定；基础设施文件不返回。 */
export type FileViewRole =
  | "structure"
  | "referenced"
  | "history"
  | "unreferenced"
  | "derived"
  | "agent_config"
  | "memory";

/** 文件所属的界面对象，供「跳到所属界面」使用。 */
export interface FileViewOwner {
  kind: "asset" | "segment" | "reference_unit" | "grid" | "episode" | "timeline" | "source" | "memory" | "project";
  id?: string;
  episode?: number;
  asset_type?: "character" | "scene" | "prop" | "product";
}

export interface FileViewEntry {
  name: string;
  /** 项目内相对 POSIX 路径。 */
  path: string;
  type: "file" | "directory";
  /** 字节数；目录是其中可见文件的总和。 */
  size: number;
  /** 带时区偏移的 UTC ISO 8601。 */
  modified_at: string;
  /** 只有文件带角色。 */
  role?: FileViewRole;
  owner?: FileViewOwner | null;
  /** 历史版本里的当前版本快照才带，且恒为 true；服务端拒绝删除它。 */
  is_current?: true;
}

export interface FileViewListing {
  path: string;
  entries: FileViewEntry[];
}

// 项目回收站（后端 lib/project/project_trash.py）。

/** 一条删除记录。`object.kind` 是被删对象的类型，文件视图删除的未引用文件为 `file`。 */
export interface TrashRecord {
  id: string;
  object: { kind: string; path?: string; [key: string]: unknown };
  /** 被删文件删除前的项目内相对路径。 */
  files: string[];
  schema_version: number;
  /** 带时区偏移的 UTC ISO 8601。 */
  deleted_at: string;
  origin: "user" | "agent";
  size: number;
  has_snapshot: boolean;
  /** 无法按原结构恢复、已降级恢复过的记录：只剩结构文件，只能下载。 */
  download_only: boolean;
}

export interface TrashListing {
  records: TrashRecord[];
  total_size: number;
  retention_days: number;
}

// 已删除项目（后端 lib/project/deleted_projects.py）。

/** 一个已删除项目。`id` 是它的墓碑名，恢复与彻底删除都按它定位；`name` 是删除前的项目名。 */
export interface DeletedProject {
  id: string;
  name: string;
  title: string;
  /** 带时区偏移的 UTC ISO 8601。 */
  deleted_at: string;
  /** 到期自动清除的时间，同为 UTC ISO 8601。 */
  expires_at: string;
  /** 项目目录占用的字节数。 */
  size: number;
}

export interface DeletedProjectListing {
  projects: DeletedProject[];
  retention_days: number;
}

/** 恢复后一个文件的落点：`as_history` 为真时它成了所在资源的一条历史版本。 */
export interface RestoredFile {
  original: string;
  path: string;
  as_history: boolean;
}

/** 一次恢复的结果。`object` 是加回后的对象描述，换了名时与记录里的不同。 */
export interface RestoreResult {
  object: TrashRecord["object"];
  restored: RestoredFile[];
  /** 为真时对象无法按原结构恢复，只加回了其中的文件。 */
  degraded: boolean;
  /** 留在记录里、只能下载的结构文件。 */
  download_only: string[];
}
