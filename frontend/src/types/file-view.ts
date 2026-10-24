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
}

export interface FileViewListing {
  path: string;
  entries: FileViewEntry[];
}
