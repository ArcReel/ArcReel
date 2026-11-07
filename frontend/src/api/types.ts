/**
 * Shared API types: payloads, query filters and cross-endpoint response structures.
 * Reexport through `@/api`, retaining caller imports from `@/api`.
 */

import type {
  AssistantSessionResumedPayload,
  EpisodeScript,
  NarrationDelivery,
  ProjectChangeBatchPayload,
  ProjectDeletedPayload,
  ProjectEventSnapshotPayload,
} from "@/types";
import type { GenerationRoute } from "@/utils/generation-mode";
import type { SseStreamError } from "@/utils/sse-stream";

/** Four project asset kinds, matching backend ASSET_SPECS asset_type. */
export type ProjectAssetType = "character" | "scene" | "prop" | "product";

/** Mergeable aliased asset kinds; products excluded, matching backend MERGEABLE_ASSET_TYPES. */
export type MergeableAssetType = Exclude<ProjectAssetType, "product">;

/** Cascading asset-rename impacts, identical for dry_run previews/execution. */
export interface AssetRenameResult {
  success: boolean;
  dry_run: boolean;
  old_name: string;
  new_name: string;
  episodes: number;
  references: number;
  files: number;
}

/** Per-episode merge impacts: rewritten references and stale storyboard/video counts. */
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

/** Asset-merge impacts, identical for dry_run previews/execution. */
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

/** Predelete references (`DELETE …?dry_run=true`), sharing rename scans and ascending episode-number order. */
export interface AssetDeletionPreview {
  success: boolean;
  dry_run: true;
  name: string;
  /** Total reference sites, possibly exceeding episode sums for unattributed references. */
  references: number;
  episodes: { episode: number; references: number }[];
}

/** Login response from POST /auth/token (mirrors backend TokenResponse). */
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

/** Version metadata returned by the versions API. */
export interface VersionInfo {
  version: number;
  filename: string;
  created_at: string;
  file_size: number;
  is_current: boolean;
  /** Whether this history record carries verified provenance for restore. */
  restorable?: boolean;
  /** Whether the shared presentation reader can preview/export this video version. */
  presentation_available?: boolean;
  file_url?: string;
  prompt?: string;
  restored_from?: number;
  /** Version origins; "manual_upload" means user uploads. */
  source?: string;
}

/** Unified storyboard/video-unit media-upload responses. */
export interface ShotUploadResult {
  success: boolean;
  path: string;
  version: number;
  asset_fingerprints: Record<string, number>;
}

export interface ProjectEventStreamOptions {
  projectName: string;
  /** Servers first send snapshots on every connection, including reconnects. */
  onSnapshot?: (payload: ProjectEventSnapshotPayload) => void;
  onChanges?: (payload: ProjectChangeBatchPayload) => void;
  /** Sent once on project-directory deletion, followed by normal stream closure; subscribers close handles to stop reconnects. */
  onProjectDeleted?: (payload: ProjectDeletedPayload) => void;
  /** Agent sessions independently return running; active sessions receive entry streams, this serves lists/other views. */
  onAssistantSessionResumed?: (payload: AssistantSessionResumedPayload) => void;
  /** Failed/interrupted connections; true `retryable` automatically reconnects. */
  onError?: (error: SseStreamError) => void;
}

export interface AssistantEntriesStreamOptions {
  projectName: string;
  sessionId: string;
  /** Cold subscription seq cursors; reconnection continuation uses `Last-Event-ID`. */
  after?: number;
  onEvent: (event: string, payload: Record<string, unknown>) => void;
  onError?: (error: SseStreamError) => void;
}

/** Filters for {@link API.listTasks} and {@link API.listProjectTasks}. */
export interface TaskListFilters {
  projectName?: string;
  status?: string;
  taskType?: string;
  source?: string;
  page?: number;
  pageSize?: number;
}

/** {@link API.getUsageRecords} filters; arrays use query commas. */
export interface UsageRecordsQuery {
  /** Empty strings filter endpoint trials, `undefined` means no project filter. */
  projectName?: string;
  providers?: readonly string[];
  models?: readonly string[];
  mediaTypes?: readonly string[];
  statuses?: readonly string[];
  segmentIds?: readonly string[];
  /** ISO8601 half-open [since, until) instants, applied to started_at. */
  since?: string;
  until?: string;
  limit?: number;
  /** Opaque prior-page cursors. */
  cursor?: string;
  /** Views default Generation calls, excluding Agent records. */
  view?: "generation" | "agent";
}

/** {@link API.getUsageSessions} filters match records, without status/media/storyboards. */
export interface UsageSessionsQuery {
  /** Empty strings filter endpoint trials, `undefined` means no project filter. */
  projectName?: string;
  providers?: readonly string[];
  models?: readonly string[];
  since?: string;
  until?: string;
  limit?: number;
  cursor?: string;
}

/** {@link API.getUsageSummary} filters exclude status; pending never aggregates. */
export interface UsageSummaryQuery {
  /** Empty strings filter endpoint trials, `undefined` means no project filter. */
  projectName?: string;
  provider?: string;
  model?: string;
  mediaType?: string;
  since?: string;
  until?: string;
  /** IANA timezone day boundaries, default UTC. */
  tz?: string;
  /** Views controlling project/provider/model filter candidates; totals remain cross-view. */
  view?: "generation" | "agent";
}

/** Generic success response used by many endpoints. */
export interface SuccessResponse {
  success: boolean;
  message?: string;
}

/** Created assets include latest matching kind/name trash deletion IDs when present. */
export interface AssetCreateResult extends SuccessResponse {
  trash_record_id?: string | null;
}

export interface AgentProfileStatus {
  customized: boolean;
  customized_files: string[];
}

/** Payload for {@link API.createProject}. */
export interface CreateProjectPayload {
  title: string;
  name?: string;
  content_mode?: "narration" | "drama" | "ad";
  aspect_ratio?: "9:16" | "16:9";
  /** Required binary creation modes without defaults; omitted values receive backend 422. */
  generation_mode: GenerationRoute;
  /** Creation-time multigrid assembly switches, meaningful only for storyboard image-to-video. */
  grid_storyboard?: boolean;
  /** Estimated reading units/second; blank uses project-language defaults. */
  speech_rate_units_per_second?: number | null;
  default_duration?: number | null;
  /** Optional episode target seconds; omit unset. Servers reject this for ads. */
  episode_target_duration?: number | null;
  /** Ads only: total target seconds, UI tiers 15/30/60/90. */
  target_duration?: number;
  /** Ads only: optional short creative briefs. */
  brief?: string | null;
  style_template_id?: string | null;
  video_backend?: string | null;
  image_backend?: string | null;
  /** Default project image models; wizards expose defaults only (docs/adr/0054), buckets remain in settings. */
  default_image_backend?: string | null;
  text_backend_simple?: string | null;
  text_backend_complex?: string | null;
  default_text_backend?: string | null;
  model_settings?: Record<string, { resolution?: string | null }>;
  /** Narration delivery defaults to postproduction dubbing on servers. */
  narration_delivery?: NarrationDelivery;
  /** use_tts-only snapshots; omitted fields prefill global defaults on servers. */
  audio_backend?: string;
  narration_voice?: string;
  narration_speed?: number | null;
}

export interface VideoCapabilitiesQuery {
  signal?: AbortSignal;
  videoBackend?: string;
  /** undefined = saved project tiers; null = explicit Auto, sending empty strings; strings = specified tiers. */
  resolution?: string | null;
  usesReferenceImages?: boolean;
}
