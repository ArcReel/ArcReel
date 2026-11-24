
import type { CostBreakdown } from "./cost";
import type { DurationExclusionReason, VideoCapabilityProblem } from "./project";
import type { PlanNewAsset, RenderedPromptPreview } from "./script";
import type {
  AdmissionProblem,
  WorkflowAdmission,
} from "./workflow";

export type AssetKind = "product" | "character" | "scene" | "prop";

export const SHEET_FIELD: Record<AssetKind, "product_sheet" | "character_sheet" | "scene_sheet" | "prop_sheet"> = {
  product: "product_sheet",
  character: "character_sheet",
  scene: "scene_sheet",
  prop: "prop_sheet",
};

export type UnitPersistedStatus = "pending" | "storyboard_ready" | "completed";

export type UnitStatus = "pending" | "running" | "ready" | "failed";

export interface UnitGeneratedAssets {
  storyboard_image: string | null;
  storyboard_last_image: string | null;
  grid_id: string | null;
  grid_cell_index: number | null;
  video_clip: string | null;
  video_uri: string | null;
  video_thumbnail?: string | null;
  narration_audio?: string | null;
  status: UnitPersistedStatus;
  video_generated_at: string | null;
  source_signature?: string | null;
}

export interface ReferenceVideoUnit {
  unit_id: string;
  text: string;
  duration_seconds: number;
  note: string | null;
  generated_assets?: UnitGeneratedAssets;
  needs_replan?: boolean;
  pending_authoring?: boolean;
  source_text?: string;
}

export type ReferenceVideoBucket = "i2v" | "r2v";

export interface ReferenceDeclaredResource {
  type: string;
  name: string;
}

export interface ReferenceUnitCapability {
  unit_id: string;
  declared_capability: ReferenceVideoBucket;
  hydrated_capability: ReferenceVideoBucket;
  declared_references: ReferenceDeclaredResource[];
  unavailable_references: ReferenceDeclaredResource[];
  unregistered_references: string[];
  allowed_durations: number[] | null;
  excluded_durations: Record<string, DurationExclusionReason> | null;
  duration_endpoint_fixed: boolean;
  duration_endpoint_fixed_reason: string | null;
  problem: VideoCapabilityProblem | null;
  problems: ReferenceProjectionProblem[];
}

export type ReferenceUnitCapabilityMap = Record<string, ReferenceUnitCapability>;

export interface ReferenceGenerationRequestOptions {
  confirmed_request_duration_seconds?: number | null;
}

export interface ReferenceProjectionLocation {
  path: (string | number)[];
  line: number | null;
}

export interface ReferenceProjectionProblem {
  code: string;
  blocking: boolean;
  unit_id: string;
  locations: ReferenceProjectionLocation[];
  params: Record<string, unknown>;
  reason?: string;
  action: string;
  message?: string;
}

export interface ReferenceProjectionAdmission {
  allowed: false;
  kind: "reference_request_projection";
  unit_id: string;
  problems: ReferenceProjectionProblem[];
}

export interface ReferenceDurationPrecheck {
  needs_confirmation: boolean;
  script_duration: number;
  duration_input: number;
  request_duration: number;
  adjustment: "exact" | "up" | "down";
  declared_capability: "i2v" | "r2v";
  hydrated_capability: "i2v" | "r2v";
  provider_id: string | null;
  model_id: string | null;
  problems: ReferenceProjectionProblem[];
}

export interface ReferenceBatchEnqueueFailure {
  unit_id: string;
  problem: AdmissionProblem;
}

export interface ReferenceBatchAdmission extends WorkflowAdmission {
  skipped_unit_ids: string[];
  task_ids: string[];
  task_ids_by_unit: Record<string, string>;
  enqueue_failures: ReferenceBatchEnqueueFailure[];
  deduped: boolean;
}

export interface ReferenceBatchPreview extends WorkflowAdmission {
  skipped_unit_ids: string[];
  estimated_cost: CostBreakdown | null;
  unpriced_count: number;
}

export interface ReferenceBatchGenerateRequest {
  unit_ids?: string[];
  confirmed_request_durations?: Record<string, number>;
}

export type ScriptPreviewUtterance =
  | { index: number; kind: "dialogue"; speaker: string; text: string }
  | { index: number; kind: "voiceover"; speaker: null; text: string };

export interface ScriptPreviewWarning {
  key: string;
  message: string;
}

export interface ScriptPreview {
  utterances: ScriptPreviewUtterance[];
  warnings: ScriptPreviewWarning[];
}

export interface ReferenceScriptPlanUnit {
  unit_id: string;
  text: string;
  duration_seconds: number;
  source_text: string;
}

export interface ReferenceScriptPlanDraft {
  units: ReferenceScriptPlanUnit[];
  new_assets?: PlanNewAsset[];
}

export interface ReferenceScriptPlanFlatUnit {
  duration_seconds: number;
  source_text: string;
  text: string;
}

export interface ScriptReviewViolation {
  code: string;
  label: string;
  message: string;
  line: number | null;
  locations?: Array<{ path: Array<string | number>; line: number | null }>;
  reason?: string;
  action?: string;
  item_index?: number | null;
  item_id?: string | null;
  params?: Record<string, unknown>;
}

export interface DraftSoftViolation {
  code: string;
  params: Record<string, unknown>;
  item_index: number;
  item_id: string;
  message: string;
}

export type DraftDocType =
  | "drama_script_plan"
  | "narration_script_plan"
  | "reference_script_plan"
  | "reference_prompt_authoring";

export type DraftOwner = "user" | "agent";

export interface ScriptReviewQuarantine {
  doc_type: DraftDocType;
  revision: string | null;
  editable_by: DraftOwner;
  content: Record<string, unknown> | null;
  violations: ScriptReviewViolation[];
  soft_violations: DraftSoftViolation[];
  formal_exists: boolean;
}

export interface EpisodeDraftView extends ScriptReviewQuarantine {
  episode: number;
  item_ids: string[] | null;
}

export interface EpisodeDraftSummary {
  doc_type: DraftDocType;
  editable_by: DraftOwner;
  violation_count: number;
}

export interface SaveEpisodeDraftResult {
  episode: number;
  doc_type: DraftDocType;
  adopted: boolean;
  draft: EpisodeDraftView | null;
}


export interface ReferenceUnitPromptPreview extends RenderedPromptPreview {
  references: { type: AssetKind; name: string; path: string }[];
}
