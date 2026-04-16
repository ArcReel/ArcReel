export type ProjectEventSource = "webui" | "worker" | "filesystem";

export interface ProjectChangeFocus {
  pane: "characters" | "scenes" | "props" | "episode";
  episode?: number;
  anchor_type?: "character" | "scene" | "prop" | "segment";
  anchor_id?: string;
  tab?: string;
}

export interface ProjectChange {
  entity_type: "project" | "character" | "scene" | "prop" | "segment" | "episode" | "overview" | "draft" | "grid";
  action:
    | "created"
    | "updated"
    | "deleted"
    | "storyboard_ready"
    | "video_ready"
    | "grid_ready";
  entity_id: string;
  label: string;
  script_file?: string;
  episode?: number;
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

export interface WorkspaceFocusTarget {
  request_id: string;
  type: "character" | "scene" | "prop" | "segment";
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
