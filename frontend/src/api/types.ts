
import type {
  TaskProgress,
  AssistantSessionResumedPayload,
  EpisodeScript,
  ModelSettingEntry,
  NarrationDelivery,
  ProjectChangeBatchPayload,
  ProjectDeletedPayload,
  ProjectEventSnapshotPayload,
} from "@/types";
import type { GenerationRoute } from "@/utils/generation-mode";
import type { SseStreamError } from "@/utils/sse-stream";

export type ProjectAssetType = "character" | "scene" | "prop" | "product";

export type MergeableAssetType = Exclude<ProjectAssetType, "product">;

export interface AssetRenameResult {
  success: boolean;
  dry_run: boolean;
  old_name: string;
  new_name: string;
  episodes: number;
  references: number;
  files: number;
}

export interface AssetMergeEpisodeImpact {
  episode: number;
  script_plan: number;
  script: number;
  draft: number;
  prompt_text: number;
  speaker: number;
  storyboards: number;
  videos: number;
}

export interface AssetMergeResult {
  success: boolean;
  dry_run: boolean;
  source: string;
  target: string;
  as_derivative: boolean;
  aliases_added: string[];
  derivative_created: string | null;
  derivatives_moved: string[];
  derivatives_folded: string[];
  references: number;
  episodes: AssetMergeEpisodeImpact[];
}

export interface AssetDeletionPreview {
  success: boolean;
  dry_run: true;
  name: string;
  references: number;
  episodes: { episode: number; references: number }[];
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
}

export type ScriptEditOperation =
  | { op: "update"; id: string; fields: Record<string, unknown> }
  | { op: "insert_after"; after_id: string | null; item: Record<string, unknown> }
  | { op: "move_after"; id: string; after_id: string | null }
  | { op: "remove"; id: string };

export interface ScriptEditCommand {
  script?: string;
  episode?: number;
  expected_revision: string;
  operations: ScriptEditOperation[];
}

export interface EpisodeScriptSnapshot {
  script: EpisodeScript;
  revision: string;
}

export interface VersionInfo {
  version: number;
  filename: string;
  file?: string;
  created_at: string;
  file_size: number;
  is_current: boolean;
  restorable?: boolean;
  presentation_available?: boolean;
  file_url?: string;
  prompt?: string;
  restored_from?: number;
  source?: string;
}

export interface ShotUploadResult {
  success: boolean;
  path: string;
  version: number;
  asset_fingerprints: Record<string, number>;
}

export interface ProjectEventStreamOptions {
  projectName: string;
  onSnapshot?: (payload: ProjectEventSnapshotPayload) => void;
  onTaskProgress?: (payload: TaskProgress) => void;
  onChanges?: (payload: ProjectChangeBatchPayload) => void;
  onProjectDeleted?: (payload: ProjectDeletedPayload) => void;
  onAssistantSessionResumed?: (payload: AssistantSessionResumedPayload) => void;
  onError?: (error: SseStreamError) => void;
}

export interface AssistantEntriesStreamOptions {
  projectName: string;
  sessionId: string;
  after?: number;
  onEvent: (event: string, payload: Record<string, unknown>) => void;
  onError?: (error: SseStreamError) => void;
}

export interface TaskListFilters {
  resourceId?: string;
  projectName?: string;
  status?: string;
  taskType?: string;
  source?: string;
  page?: number;
  pageSize?: number;
}

export interface UsageRecordsQuery {
  projectName?: string;
  providers?: readonly string[];
  models?: readonly string[];
  mediaTypes?: readonly string[];
  statuses?: readonly string[];
  segmentIds?: readonly string[];
  since?: string;
  until?: string;
  limit?: number;
  cursor?: string;
  view?: "generation" | "agent";
}

export interface UsageSessionsQuery {
  projectName?: string;
  providers?: readonly string[];
  models?: readonly string[];
  since?: string;
  until?: string;
  limit?: number;
  cursor?: string;
}

export interface UsageSummaryQuery {
  projectName?: string;
  provider?: string;
  model?: string;
  mediaType?: string;
  since?: string;
  until?: string;
  tz?: string;
  view?: "generation" | "agent";
}

export interface SuccessResponse {
  success: boolean;
  message?: string;
}

export interface AssetCreateResult extends SuccessResponse {
  trash_record_id?: string | null;
}

export interface AgentProfileStatus {
  customized: boolean;
  customized_files: string[];
}

export interface CreateProjectPayload {
  title: string;
  name?: string;
  content_mode?: "narration" | "drama" | "ad";
  aspect_ratio?: "9:16" | "16:9";
  generation_mode: GenerationRoute;
  grid_storyboard?: boolean;
  speech_rate_units_per_second?: number | null;
  default_duration?: number | null;
  episode_target_duration?: number | null;
  target_duration?: number;
  brief?: string | null;
  style_template_id?: string | null;
  video_backend?: string | null;
  image_backend?: string | null;
  default_image_backend?: string | null;
  text_backend_simple?: string | null;
  text_backend_complex?: string | null;
  default_text_backend?: string | null;
  model_settings?: Record<string, ModelSettingEntry>;
  narration_delivery?: NarrationDelivery;
  audio_backend?: string;
  narration_voice?: string;
  narration_speed?: number | null;
}

export interface VideoCapabilitiesQuery {
  signal?: AbortSignal;
  videoBackend?: string;
  resolution?: string | null;
  usesReferenceImages?: boolean;
}
