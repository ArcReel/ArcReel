import type { PresentationResourceType } from "./presentation";

export type ProjectEventSource = "webui" | "worker" | "filesystem";

export type ProjectChangePane = "characters" | "scenes" | "props" | "products" | "episode";

export interface ProjectChangeFocus {
  pane: ProjectChangePane;
  episode?: number;
  anchor_type?: "character" | "scene" | "prop" | "product" | "segment" | "reference_unit";
  anchor_id?: string;
  tab?: string;
}

export interface ProjectChange {
  entity_type:
    | "project"
    | "character"
    | "scene"
    | "prop"
    | "product"
    | "segment"
    | "drama_scene"
    | "shot"
    | "reference_unit"
    | "episode"
    | "script_plan"
    | "overview"
    | "draft"
    | "grid"
    | "task"
    | "usage_record"
    | "trash";
  action:
    | "created"
    | "updated"
    | "deleted"
    | "storyboard_ready"
    | "video_ready"
    | "grid_ready"
    | "grid_split_done"
    | "reference_video_ready"
    | "tts_ready"
    | "voice_sample_ready"
    | "task_succeeded"
    | "task_failed"
    | "task_cancelled"
    | "recorded";
  entity_id: string;
  label: string;
  label_key?: string;
  label_params?: Record<string, string | number>;
  script_file?: string;
  episode?: number;
  task_type?: string;
  status?: string;
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

export interface ProjectDeletedPayload {
  project_name: string;
}

export interface AssistantSessionResumedPayload {
  project_name: string;
  session_id: string;
  status: "running";
}

export interface WorkspaceFocusTarget {
  request_id: string;
  type: "character" | "scene" | "prop" | "product" | "segment" | "grid" | "reference_unit";
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

export interface PlaybackStartRequest {
  request_id: string;
  resource_type: PresentationResourceType;
  resource_id: string;
  seconds: number | null;
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
