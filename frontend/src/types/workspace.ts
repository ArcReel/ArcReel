import type { PresentationResourceType } from "./presentation";

export type ProjectEventSource = "webui" | "worker" | "filesystem";

/** Target pane for project events: assets use their asset table names (covering the backend asset type table), while script entries use episode. */
export type ProjectChangePane = "characters" | "scenes" | "props" | "products" | "episode";

export interface ProjectChangeFocus {
  pane: ProjectChangePane;
  episode?: number;
  // The segment/drama_scene/shot skeleton entries use the timeline canvas with anchor type segment; video_units use the reference
  // video canvas with anchor type reference_unit (aligned with WorkspaceFocusTarget["type"] and canvas guards).
  anchor_type?: "character" | "scene" | "prop" | "product" | "segment" | "reference_unit";
  anchor_id?: string;
  tab?: string;
}

export interface ProjectChange {
  // segment/drama_scene/shot/reference_unit are the four script skeleton entry types (narration/drama/ad/reference video),
  // driving group label mappings; drama uses drama_scene to avoid grouping collisions with the named entity scene.
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
    | "overview"
    | "draft"
    | "grid"
    // task is a refresh signal for a terminal task state rather than a project entity (important=false / focus=null):
    // used only to refetch the task list and affected canvases, without notifications or focus navigation.
    | "task"
    // usage_record is also a refresh signal: a provider call has been settled and persisted (success/failure/cancellation),
    // used to refetch usage and costs, without notifications or focus navigation.
    | "usage_record"
    // trash is also a refresh signal: additions or removals in project trash trigger refetching the file view and trash section.
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
  /** Entry label rendered by the backend in the default language, used only for logs and legacy publisher fallback; UI wording uses label_key. */
  label: string;
  /** Stable entry label identifier corresponding to `label.*` in the `events` namespace. */
  label_key?: string;
  /** Interpolation parameters for label_key (such as id / episode). */
  label_params?: Record<string, string | number>;
  script_file?: string;
  episode?: number;
  /** Present only for entity_type === "task": the terminal task's type, used to determine which canvas kind needs refetching. */
  task_type?: string;
  /** Present only for entity_type === "usage_record": the terminal state in which this call was settled. */
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

/** Payload ending the project event stream, sent after the project directory is deleted; the stream then ends normally. */
export interface ProjectDeletedPayload {
  project_name: string;
}

/** Sent when a project's Agent session starts a new turn without a user message (awakened after a background task completes). */
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

/**
 * Request to open a video unit preview (from a unit link in an Agent reply): bring the preview forward when it is in a subtab on narrow screens;
 * when `seconds` is not null, start playback from that time. One-shot: cleared after the player consumes it, and expires if not consumed in time.
 */
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
