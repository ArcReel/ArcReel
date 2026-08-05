export type ProjectEventSource = "webui" | "worker" | "filesystem";

export interface ProjectChangeFocus {
  pane: "characters" | "scenes" | "props" | "episode";
  episode?: number;
  // segment/drama_scene/shot 三种骨架条目走时间线画布，锚点类型统一为 segment；video_units 走参考
  // 生视频画布，锚点类型为 reference_unit（与 WorkspaceFocusTarget["type"] 及画布守卫对齐）。
  anchor_type?: "character" | "scene" | "prop" | "segment" | "reference_unit";
  anchor_id?: string;
  tab?: string;
}

export interface ProjectChange {
  // segment/drama_scene/shot/reference_unit 为四种剧本骨架条目类型（narration/drama/ad/参考生视频），
  // 驱动分组标签映射；drama 用 drama_scene 避免与命名实体 scene 撞组。
  entity_type:
    | "project"
    | "character"
    | "scene"
    | "prop"
    | "segment"
    | "drama_scene"
    | "shot"
    | "reference_unit"
    | "episode"
    | "overview"
    | "draft"
    | "grid"
    // task 不是项目实体，而是任务终态的刷新信号（important=false / focus=null）：
    // 只用来重拉任务列表与受影响画布，不进通知与聚焦跳转。
    | "task";
  action:
    | "created"
    | "updated"
    | "deleted"
    | "storyboard_ready"
    | "video_ready"
    | "grid_ready"
    | "reference_video_ready"
    | "tts_ready"
    | "voice_sample_ready"
    | "task_succeeded"
    | "task_failed"
    | "task_cancelled";
  entity_id: string;
  label: string;
  script_file?: string;
  episode?: number;
  /** 仅 entity_type === "task" 携带：终态任务的任务类型，用于判定哪类画布需重拉。 */
  task_type?: string;
  focus?: ProjectChangeFocus | null;
  important: boolean;
  asset_fingerprints?: Record<string, number>;
}

export interface ProjectChangeBatchPayload {
  project_name: string;
  batch_id: string;
  fingerprint: string;
  generated_at: string;
  source: ProjectEventSource;
  changes: ProjectChange[];
}

export interface ProjectEventSnapshotPayload {
  project_name: string;
  fingerprint: string;
  generated_at: string;
}

/** 项目事件流终止事件负载——项目目录被删除后下发，随后流正常结束。 */
export interface ProjectDeletedPayload {
  project_name: string;
}

export interface WorkspaceFocusTarget {
  request_id: string;
  type: "character" | "scene" | "prop" | "segment" | "grid" | "reference_unit";
  id: string;
  route: string;
  highlight: true;
  highlight_style: "flash";
  expires_at: number;
}

export interface WorkspaceFocusTargetInput {
  request_id?: string;
  type: WorkspaceFocusTarget["type"];
  id: string;
  route?: string;
  highlight?: boolean;
  highlight_style?: WorkspaceFocusTarget["highlight_style"];
  expires_at?: number;
}

export interface WorkspaceNotificationTarget {
  type: WorkspaceFocusTarget["type"];
  id: string;
  route: string;
  highlight_style?: WorkspaceFocusTarget["highlight_style"];
}

export interface WorkspaceNotification {
  id: string;
  text: string;
  tone: "info" | "success" | "error" | "warning";
  created_at: number;
  read: boolean;
  target?: WorkspaceNotificationTarget | null;
}

export interface WorkspaceNotificationInput {
  text: string;
  tone?: WorkspaceNotification["tone"];
  target?: WorkspaceNotification["target"];
  read?: boolean;
}
