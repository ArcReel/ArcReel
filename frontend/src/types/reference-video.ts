/**
 * Reference-to-video unit types — mirrors lib/script/script_models.py Pydantic models.
 *
 * One "unit" produces one rendered video clip. Its body (`text`) is the single
 * source of truth: reference images are resolved from the `@[名称]` mentions at
 * execution time and never persisted or transported.
 */

import type { CostBreakdown } from "./cost";
import type { DurationExclusionReason, VideoCapabilityProblem } from "./project";
import type { PlanNewAsset, RenderedPromptPreview } from "./script";
import type {
  AdmissionProblem,
  WorkflowAdmission,
} from "./workflow";

export type AssetKind = "product" | "character" | "scene" | "prop";

/** Project.json sheet field for each asset kind. Mirrors lib/project/asset_types.py SHEET_KEY. */
export const SHEET_FIELD: Record<AssetKind, "product_sheet" | "character_sheet" | "scene_sheet" | "prop_sheet"> = {
  product: "product_sheet",
  character: "character_sheet",
  scene: "scene_sheet",
  prop: "prop_sheet",
};

/**
 * Raw persisted status value returned by the backend in `generated_assets.status`.
 * Mirrors lib/script/script_models.py:GeneratedAssets.status Pydantic Literal exactly.
 * Note: "storyboard_ready" never appears for reference_video units — it's a legacy
 * storyboard-mode value retained in the shared GeneratedAssets model.
 */
export type UnitPersistedStatus = "pending" | "storyboard_ready" | "completed";

/**
 * UI-derived status shown in the UnitList status dot and preview panel.
 * Composed from (persisted status + task-queue state + error signals) by UI code.
 * Not sent to or received from the backend.
 */
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
  /** Raw backend status — use `UnitStatus` for UI display. */
  status: UnitPersistedStatus;
  /** ISO8601 completion time; null is treated as "before any voice setting". */
  video_generated_at: string | null;
  /** Legacy migration history only; runtime never reads or creates it. */
  source_signature?: string | null;
}

export interface ReferenceVideoUnit {
  /** Format: "E{episode}U{index}" */
  unit_id: string;
  /** Unit body — free-form text carrying `@[名称]` mentions; the only persisted content truth. */
  text: string;
  /** Planning duration in seconds — provider request duration is resolved during precheck. */
  duration_seconds: number;
  note: string | null;
  /** Units with no generated artifacts omit this section; the backend writes it only during generation, so readers must always allow its absence. */
  generated_assets?: UnitGeneratedAssets;
  /** Problem shell or mixed-speech marker; generation is blocked until repaired. */
  needs_replan?: boolean;
  /** Pending authoring: the unit's body has not been written by prompt authoring yet. Read-only. */
  pending_authoring?: boolean;
  /** Source text carried over from the script plan on content confirmation. Read-only; empty or absent for manually added units. */
  source_text?: string;
}

/** Task type bucket: i2v when no usable reference images exist, otherwise r2v. */
export type ReferenceVideoBucket = "i2v" | "r2v";

export interface ReferenceDeclaredResource {
  type: string;
  name: string;
}

/**
 * Server bucket determination for one unit, mirroring the envelope in lib/script/reference_video/unit_capabilities.py.
 *
 * Buckets are determined from **usable reference images** (after hydration), using execution-side criteria; the frontend does not infer them merely from registered names.
 * `problem` is a video request facts failure for the resolved bucket; `problems` contains blocking mismatches between declared references and usable reference images
 * (unregistered / missing image / changed bucket); `unavailable_references` names references with missing images.
 */
export interface ReferenceUnitCapability {
  unit_id: string;
  declared_capability: ReferenceVideoBucket;
  hydrated_capability: ReferenceVideoBucket;
  declared_references: ReferenceDeclaredResource[];
  unavailable_references: ReferenceDeclaredResource[];
  unregistered_references: string[];
  /** Narrowed tiers for the resolved bucket; null on a facts failure. An endpoint-fixed duration legitimately yields an empty set. */
  allowed_durations: number[] | null;
  excluded_durations: Record<string, DurationExclusionReason> | null;
  duration_endpoint_fixed: boolean;
  duration_endpoint_fixed_reason: string | null;
  problem: VideoCapabilityProblem | null;
  problems: ReferenceProjectionProblem[];
}

/** Per-unit results indexed by `unit_id`. */
export type ReferenceUnitCapabilityMap = Record<string, ReferenceUnitCapability>;

export interface ReferenceGenerationRequestOptions {
  /** Exact video tier accepted for this request; omitted when no cross-tier confirmation is needed. */
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

/**
 * Duration tier precheck result. `adjustment` describes how requested seconds differ from tier-selection input:
 * `exact` matches, `up` produces a longer clip, `down` a shorter clip. Unparseable capability metadata fails precheck immediately.
 */
export interface ReferenceDurationPrecheck {
  /** True when the requested tier differs from the current visual tier (or script tier when no output exists) */
  needs_confirmation: boolean;
  /** Script arrangement duration (seconds) */
  script_duration: number;
  /** Tier-selection input, namely the script arrangement duration */
  duration_input: number;
  /** Tier duration in seconds to request from the model */
  request_duration: number;
  adjustment: "exact" | "up" | "down";
  declared_capability: "i2v" | "r2v";
  hydrated_capability: "i2v" | "r2v";
  provider_id: string | null;
  model_id: string | null;
  problems: ReferenceProjectionProblem[];
}

/**
 * A target that could not be enqueued. Already created tasks are not withdrawn and execute normally; units listed here
 * have no tasks or charges this time, and the next "generate missing" run will include precisely these units.
 */
export interface ReferenceBatchEnqueueFailure {
  unit_id: string;
  problem: AdmissionProblem;
}

export interface ReferenceBatchAdmission extends WorkflowAdmission {
  skipped_unit_ids: string[];
  /** Nonempty only when admitted */
  task_ids: string[];
  /** Task rows per unit, allowing callers to reconcile their own optimistic occupancy markers. */
  task_ids_by_unit: Record<string, string>;
  /** Units not reached when enqueueing was interrupted; an empty array when the whole batch was enqueued. */
  enqueue_failures: ReferenceBatchEnqueueFailure[];
  deduped: boolean;
}

/** Batch preview: the same admission result as submission, plus estimated costs of priced items; creates no tasks. */
export interface ReferenceBatchPreview extends WorkflowAdmission {
  /** Units with existing videos that are skipped this time. */
  skipped_unit_ids: string[];
  /** Totals only quoted units, separately by currency; `null` when none have quotes. */
  estimated_cost: CostBreakdown | null;
  /** Number of units without quotes, excluded from `estimated_cost`. */
  unpriced_count: number;
}

/** Batch endpoint request body: omitted unit_ids means "generate missing"; an empty array is rejected by the backend. */
export interface ReferenceBatchGenerateRequest {
  unit_ids?: string[];
  /** Requested tiers confirmed by the user, keyed by unit */
  confirmed_request_durations?: Record<string, number>;
}

/**
 * Read-time derivation from a video unit's body, supplying the editor's parsed preview panel.
 *
 * The body is the sole source of truth: utterances and reference images are mechanically derived and not persisted.
 * `warnings` is rendered as text in the request language (`key` remains for tests and instrumentation).
 */
/** `index` is a 1-based utterance number, assigned in order of appearance in the body. */
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

/**
 * Structured intermediate state for reference_video script_plan (reviewable/editable at content confirmation). Maps to backend
 * ReferenceScriptPlanUnit / ReferenceScriptPlanDraft in lib/script/script_models.py: script_plan defines the content layer
 * (unit boundaries + unit durations + unit bodies); prompt_authoring visual arrangement starts only after user confirmation.
 */
export interface ReferenceScriptPlanUnit {
  unit_id: string;
  /** Unit body, using `@[名称]` to reference registered assets. */
  text: string;
  /** Unit duration in seconds — one generation call, one duration. */
  duration_seconds: number;
  /** Verbatim source excerpt (traceability anchor); may be an empty string in legacy drafts. */
  source_text: string;
}

export interface ReferenceScriptPlanDraft {
  units: ReferenceScriptPlanUnit[];
  new_assets?: PlanNewAsset[];
}

/**
 * Flat script_plan draft shape (drafts contain this rather than the persisted `ReferenceScriptPlanDraft`):
 * `unit_id` is machine-derived only before persistence; drafts contain only duration + source anchor + a body using reference syntax.
 * Mirrors lib/script/script_models.py ReferenceScriptPlanFlatUnit.
 */
export interface ReferenceScriptPlanFlatUnit {
  duration_seconds: number;
  source_text: string;
  text: string;
}

/**
 * Draft violation entry. Mirrors lib/script/draft_quarantine.py::violation_entries.
 * `label` is a location prefix such as `"unit E1U02"` (reference video, array index = derived unit number - 1) or
 * `"segment E1S03"` (narration, corresponding to `segment_id`); episode-level violations have no location and use an empty string.
 * `line` is the 0-based original line number in the unit body (in the same coordinate system as `sourceLine` in `useUnitPromptHighlight.ts`);
 * present only for syntax violations. Unit-level violations without a natural line use null and appear in the card's aggregate area.
 */
export interface ScriptReviewViolation {
  code: string;
  label: string;
  message: string;
  line: number | null;
  locations?: Array<{ path: Array<string | number>; line: number | null }>;
  reason?: string;
  action?: string;
  /** Index of the violating entry in the draft body entry array; absent together with `item_id` for episode-level violations. */
  item_index?: number | null;
  item_id?: string | null;
  /** Rendering parameters for video model configuration problems: `code` is the problem code, and the server has localized `message` from it. */
  params?: Record<string, unknown>;
}

/** Degradation notice: does not block adoption and appears with the entry. The server renders `message` in the request language. */
export interface DraftSoftViolation {
  code: string;
  params: Record<string, unknown>;
  item_index: number;
  item_id: string;
  message: string;
}

/** Document represented by the draft, using the same values as `doc_type` in Agent draft tools. */
export type DraftDocType =
  | "drama_script_plan"
  | "narration_script_plan"
  | "reference_script_plan"
  | "reference_prompt_authoring";

/** Draft handler: `user` means a draft awaiting repair (directly editable by the creator), and `agent` means an Agent-editable draft (status display only). */
export type DraftOwner = "user" | "agent";

/**
 * Draft presentation view (shared by `ScriptReviewState.quarantine` and draft endpoints): non-null only when a draft exists.
 * `content` is the unchanged draft body for in-place creator editing; script planning draft `violations` are recomputed at read time using the same validator,
 * while prompt authoring drafts use the report from the latest generation / save.
 *
 * `content` varies by route (reference video script planning `{ units }`, prompt authoring `{ title, units }`, drama
 * `{ title, scenes }`, narration `{ segments }`), and drafts may have been manually edited by the Agent, with missing or incorrectly typed fields.
 * Therefore only an object is declared here; each panel narrows its own route field by field before rendering, without trusting the declaration.
 */
export interface ScriptReviewQuarantine {
  doc_type: DraftDocType;
  /** Concurrency token for saving and discarding; null when the draft file is corrupt. */
  revision: string | null;
  editable_by: DraftOwner;
  /** Null when the draft file is corrupt or the draft is Agent-editable. */
  content: Record<string, unknown> | null;
  violations: ScriptReviewViolation[];
  soft_violations: DraftSoftViolation[];
  /** Whether formal content remains to return to after discarding. */
  formal_exists: boolean;
}

/** View returned by draft endpoints: `item_ids` contains the formal script unit IDs corresponding to prompt authoring draft entries. */
export interface EpisodeDraftView extends ScriptReviewQuarantine {
  episode: number;
  item_ids: string[] | null;
}

export interface EpisodeDraftSummary {
  doc_type: DraftDocType;
  editable_by: DraftOwner;
  violation_count: number;
}

/** Manual repair save result: adopted when no violations remain (`draft` is null); otherwise returns the refreshed draft view. */
export interface SaveEpisodeDraftResult {
  episode: number;
  doc_type: DraftDocType;
  adopted: boolean;
  draft: EpisodeDraftView | null;
}


/** Final text and actual images after projecting the current draft against model capabilities; image order matches image numbers in the prompt. */
export interface ReferenceUnitPromptPreview extends RenderedPromptPreview {
  references: { type: AssetKind; name: string; path: string }[];
}
