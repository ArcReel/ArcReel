/**
 * Frontend projection of the workflow plan. Fields correspond exactly to backend `lib/workflow/workflow_plan.py` / `lib/workflow/workflow_state.py` /
 * `lib/generation/generation_result.py`; REST and MCP share serialization without a top-level envelope.
 *
 * The three axes are separated at the type level; the UI must not collapse them into one state:
 * - Step progress `WorkflowStepState`: how far orchestration has progressed
 * - Artifact currency `ArtifactStatus`: whether the on-disk artifact still represents current content
 * - Task and provider submission: how far this attempt has progressed and whether the provider has accepted the request
 */

/** Progress of the orchestration step itself, independent of the artifact and task axes. */
export type WorkflowStepState =
  | "completed"
  | "ready"
  | "active"
  | "blocked"
  | "pending"
  | "skipped";

/** Artifact currency. stale is a warning that the artifact remains usable, not a missing artifact or authorization to regenerate automatically. */
export type ArtifactStatus = "current" | "stale" | "missing" | "blocked";

/** Narration delivery: project configuration (docs/adr/0089). */
export type NarrationDelivery = "post_production" | "use_tts";

/**
 * Closed set of next-action identifiers returned by the backend, corresponding exactly to `WorkflowActionType` in `lib/workflow/workflow_state.py`;
 * backend contract tests keep both sides synchronized. A runtime array rather than just a type lets translation coverage
 * checks iterate every action: adding an action without copy fails the test instead of silently using fallback wording.
 */
export const WORKFLOW_ACTION_TYPES = [
  "none",
  "collect_project_input",
  "create_episode",
  "draft_selling_points",
  "plan_episodes",
  "reset_episode_planning",
  "resolve_draft",
  "prepare_script_plan",
  "start_blank_script",
  "provide_episode_source",
  "confirm_script_plan",
  "generate_script",
  "add_script_items",
  "author_prompts",
  "generate_asset_sheets",
  "generate_storyboards",
  "generate_grid",
  "repair_video_units",
  "generate_videos",
  "create_edit_timeline",
  "retry_project_migration",
  "patch_episode_script",
  "retry",
  "fix_input",
  "generate_dependency",
  "wait_for_task",
  "replan_unit",
  "confirm_request_duration",
  "configure_provider",
  "repair_artifact_state",
  "retry_artifact_download",
] as const;

export type WorkflowActionType = (typeof WORKFLOW_ACTION_TYPES)[number];

export interface WorkflowNextAction {
  type: WorkflowActionType;
  args: Record<string, unknown>;
  requested_ids: string[];
  requires_confirmation: boolean;
  reason: string;
}

/**
 * A blocker such as data corruption. `path` locates the specific field, while `reason` provides technical details:
 * the former goes in the summary and the latter in the collapsible details.
 */
export interface WorkflowBlocker {
  code: string;
  path: string;
  reason: string;
}

/**
 * One notice in the plan. `code` is a stable machine key localized by the UI; `params` provides copy parameters;
 * raw reason text is not sent. Each notice appears only in its own step (project-level notices are at the top level), without duplication.
 */
export interface WorkflowProblem {
  code: string;
  action: string;
  /** Affected unit; empty for project-level notices. */
  unit_id?: string | null;
  params: Record<string, unknown>;
}

/** Whether the provider has accepted the request. Acceptance means retrying may incur duplicate charges and must be stated separately from task status. */
export interface WorkflowProviderCheckpoint {
  submitted: boolean;
}

/** One queued or running task. It represents progress rather than a problem; recovering tasks stay on this axis and never count as current artifacts. */
export interface WorkflowTaskObservation {
  unit_id: string;
  task_id: string;
  batch_id?: string | null;
  task_type: string;
  status: string;
  provider_checkpoint?: WorkflowProviderCheckpoint | null;
}

/**
 * State vocabulary for artifact entries in a plan. Broader than {@link ArtifactStatus}: in addition to currency, it expresses
 * "not applicable to this mode" and "partial coverage" (for example, an asset inventory covering only part of the source).
 * Values evolve with the backend; the UI handles known values and uses fallback wording for others, without exhaustive assertions.
 */
export type WorkflowArtifactState = ArtifactStatus | "not_applicable" | "partial" | (string & {});

/**
 * Artifact collection for one step. When enumerable, three ID lists are returned; when the container itself is unreadable,
 * only `state: "blocked"` is returned, without guessing which IDs are current / stale / missing.
 * Single artifacts (script, script_plan, etc.) return only `state`.
 */
export interface WorkflowArtifactCollection {
  state?: WorkflowArtifactState;
  current_ids?: string[];
  stale_ids?: string[];
  missing_ids?: string[];
  path?: string;
  [key: string]: unknown;
}

export interface WorkflowStepContracts {
  script_edit?: "script_batch_edit/v1" | null;
  batch_admission?: "video_batch_admission/v1" | null;
}

export interface WorkflowPlanStep {
  id: string;
  state: WorkflowStepState;
  required: boolean;
  action?: WorkflowNextAction | null;
  requested_ids: string[];
  artifacts: WorkflowArtifactCollection;
  problems: WorkflowProblem[];
  tasks: WorkflowTaskObservation[];
  admission?: WorkflowAdmission | null;
  contracts: WorkflowStepContracts;
}

/**
 * Admission result for the whole batch. Both outcomes other than `admitted` mean **no tasks were created**;
 * the UI must state this explicitly rather than merely reporting a failure.
 */
export type BatchAdmissionDecision = "admitted" | "confirmation_required" | "blocked";

/**
 * One gap in an admission result, sharing the source of generation contract problems; the only difference is that
 * batch endpoints render wording in the user's language into `message`; plan endpoints do not, so the UI falls back to its translation table.
 */
export interface AdmissionProblem {
  code: string;
  detail?: string | null;
  action?: string | null;
  params?: Record<string, unknown>;
  /** Already localized in the user's language and ready to display; when absent, look up `code` in the translation table. */
  message?: string | null;
}

/** Exact provider-side cost quote for this video request. */
export interface VideoRequestCostQuote {
  /** null means the price table cannot price this request; see unpriced_reason. */
  amount: number | null;
  currency: string | null;
  provider_id: string;
  model_id: string;
  request_duration_seconds: number;
  unpriced_reason?: string | null;
}

export interface BatchAdmissionUnit {
  unit_id: string;
  /** This unit's own admission result; may still be true in a blocked batch (withheld because of other units in the batch). */
  admitted: boolean;
  withheld?: boolean;
  request_duration_seconds?: number | null;
  request_cost?: VideoRequestCostQuote | null;
  problems: AdmissionProblem[];
  projection?: unknown;
}

export interface BatchAdmissionTier {
  request_duration_seconds: number | null;
  unit_count: number;
  unit_ids: string[];
  cost_amount?: number | null;
  cost_currency?: string | null;
  /** Number of units in this tier that cannot be priced by the price table and are excluded from `cost_amount`. */
  unpriced_count?: number;
}

export interface WorkflowAdmission {
  decision: BatchAdmissionDecision;
  operation: string;
  selection: "explicit" | "missing_only";
  units: BatchAdmissionUnit[];
  confirmation?: { tiers: BatchAdmissionTier[] } | null;
}

export interface WorkflowProject {
  content_mode: string;
  generation_mode: string;
  grid_storyboard: boolean;
}

export interface WorkflowTarget {
  episode: number;
  script: string;
  script_filename: string;
  source: string;
}

/** Current structural admission for an AI operation; `reason` is a stable reason code sharing the source of refusal reasons at the operation entry point. */
export interface WorkflowOperation {
  state: "admitted" | "refused" | "not_applicable";
  reason?: string | null;
}

/** One draft present on the target episode; `needs_repair` distinguishes drafts awaiting repair from Agent-editable drafts. */
export interface WorkflowDraft {
  kind: string;
  path: string;
  needs_repair: boolean;
}

/** Current content state. Episode-level fields have values only when an episode is targeted; the entire object is empty when the project is unavailable as a whole. */
export interface WorkflowContent {
  episode_count: number;
  whole_source: "present" | "absent" | "not_applicable";
  source_remaining: boolean;
  ad_inputs: "present" | "absent" | "not_applicable";
  products_without_selling_points: string[];
  episode_source?: "present" | "absent" | "not_applicable" | null;
  episode_plan_stale: boolean;
  expected_stale_script_plan_revision?: string | null;
  drafts: WorkflowDraft[];
  formal_script?: "present" | "absent" | "invalid" | null;
  script_item_count?: number | null;
  pending_authoring_ids: string[];
  needs_replan_ids: string[];
  referenced_assets_without_sheet: string[];
  unregistered_references: string[];
  /** Assets referenced by this episode whose sheets are stale; reported only, without becoming the suggested next action. */
  referenced_asset_sheets_stale: string[];
  /** Assets referenced by this episode whose sheets cannot be generated because descriptions are missing; reported only, without becoming the suggested next action. */
  referenced_assets_without_description: string[];
  /** The target episode is complete: all videos are present and at least one edit timeline exists. */
  episode_complete?: boolean;
  /** True only in queries without a specified episode: all episodes are complete and no whole-source content remains. */
  project_complete?: boolean;
}

/** One artifact not carried over by the last migration: its file remains in place but is not registered. */
export interface WorkflowMigrationSkippedArtifact {
  kind: string;
  episode: number | null;
  resource_id: string;
  artifact_path: string;
  reason: string;
  /** Reason for not being carried over: the file is unreadable, or legacy narration audio has no recorded synthesis settings. */
  category?: "unreadable" | "legacy_audio_basis" | null;
}

/** One adjustment made to user data by migration or import; `code` determines display wording and `params` supplies its values. */
export type WorkflowMigrationAdjustmentCode =
  | "draft_name_conflict_resolved"
  | "duplicate_episode_entry_removed"
  | "shared_script_binding_split"
  | "script_episode_corrected"
  | "script_binding_canonicalized"
  | "item_id_reassigned"
  | "duplicate_asset_removed"
  | "duplicate_asset_derivative_merged"
  | "duplicate_asset_references_rewritten"
  | "shared_reference_file_copied"
  | "placeholder_asset_added";

export interface WorkflowMigrationAdjustment {
  code: WorkflowMigrationAdjustmentCode;
  episode: number | null;
  file: string | null;
  /** Retained location of the replaced copy (project-relative path); never automatically cleaned up. */
  backup: string | null;
  params: Record<string, string>;
}

/**
 * What the last migration adjusted (`adjusted`) or could not carry over (`skipped`). Informational only, without affecting state or blocking;
 * production status includes it only when the report is nonempty and not yet acknowledged by the user. Failed project migrations use `project_migration_failed` in `blockers`.
 */
export interface WorkflowMigrationReport {
  schema_version: 2;
  migrated_at: string;
  from_schema_version: number;
  to_schema_version: number;
  complete: boolean;
  registered: Record<string, number>;
  adjusted: WorkflowMigrationAdjustment[];
  skipped: WorkflowMigrationSkippedArtifact[];
  acknowledged_at: string | null;
}

/**
 * Production status: current content (`content` / `artifacts`), admission for each AI operation (`operations`), and the suggested next action
 * (`next_action`, with parallel branch choices in `next_alternatives`). `blockers` only indicates that the project as a whole is unavailable;
 * data problems in the content itself belong in `issues`.
 */
export interface WorkflowStatus {
  schema_version: 2;
  project_revision: string;
  source_revision: string | null;
  project: WorkflowProject;
  target: WorkflowTarget | null;
  blockers: WorkflowBlocker[];
  issues: WorkflowBlocker[];
  content: WorkflowContent | null;
  operations: Record<string, WorkflowOperation>;
  gates: Record<string, Record<string, unknown>>;
  artifacts: Record<string, WorkflowArtifactCollection>;
  next_action: WorkflowNextAction;
  next_alternatives: WorkflowNextAction[];
  migration_report?: WorkflowMigrationReport | null;
}

/** Suggested next action for each ledger episode (`GET /projects/{name}/workflow-status/episodes`), in ledger order. */
export interface EpisodeNextStep {
  episode: number;
  /** This episode's plan is stale (the source was replanned); the next action is `none`. */
  plan_stale: boolean;
  next_action: WorkflowNextAction;
}

export interface WorkflowPlan {
  schema_version: 2;
  status: WorkflowStatus;
  steps: WorkflowPlanStep[];
  blockers: WorkflowBlocker[];
  /** Project-level notices not belonging to any step (such as a data upgrade failure). */
  problems: WorkflowProblem[];
  next_action: WorkflowNextAction;
  next_alternatives: WorkflowNextAction[];
}

/** Request body for `POST /projects/{name}/workflow-plan`. */
export interface WorkflowPlanRequest {
  episode_id?: number | null;
  confirmed_request_durations?: Record<string, number>;
}
