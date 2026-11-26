
export type WorkflowStepState =
  | "completed"
  | "ready"
  | "active"
  | "blocked"
  | "pending"
  | "skipped";

export type ArtifactStatus = "current" | "stale" | "missing" | "blocked";

export type NarrationDelivery = "post_production" | "use_tts";

export const WORKFLOW_ACTION_TYPES = [
  "none",
  "collect_project_input",
  "create_episode",
  "draft_selling_points",
  "plan_episodes",
  "reset_episode_planning",
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
  "configure_default_model",
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

export interface WorkflowBlocker {
  code: string;
  path: string;
  reason: string;
}

export interface WorkflowProblem {
  code: string;
  action: string;
  unit_id?: string | null;
  params: Record<string, unknown>;
}

export interface WorkflowProviderCheckpoint {
  submitted: boolean;
}

export interface WorkflowTaskObservation {
  unit_id: string;
  task_id: string;
  batch_id?: string | null;
  task_type: string;
  status: string;
  provider_checkpoint?: WorkflowProviderCheckpoint | null;
}

export type WorkflowArtifactState = ArtifactStatus | "not_applicable" | "partial" | (string & {});

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

export type BatchAdmissionDecision = "admitted" | "confirmation_required" | "blocked";

export interface AdmissionProblem {
  code: string;
  detail?: string | null;
  action?: string | null;
  params?: Record<string, unknown>;
  message?: string | null;
}

export interface VideoRequestCostQuote {
  amount: number | null;
  currency: string | null;
  provider_id: string;
  model_id: string;
  request_duration_seconds: number;
  unpriced_reason?: string | null;
}

export interface BatchAdmissionUnit {
  unit_id: string;
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

export interface WorkflowOperation {
  state: "admitted" | "refused" | "not_applicable";
  reason?: string | null;
}

export interface WorkflowContent {
  episode_count: number;
  whole_source: "present" | "absent" | "not_applicable";
  source_remaining: boolean;
  ad_inputs: "present" | "absent" | "not_applicable";
  products_without_selling_points: string[];
  episode_source?: "present" | "absent" | "not_applicable" | null;
  episode_plan_stale: boolean;
  expected_stale_script_plan_revision?: string | null;
  formal_script?: "present" | "absent" | "invalid" | null;
  script_item_count?: number | null;
  pending_authoring_ids: string[];
  needs_replan_ids: string[];
  referenced_assets_without_sheet: string[];
  unregistered_references: string[];
  referenced_asset_sheets_stale: string[];
  referenced_assets_without_description: string[];
  episode_complete?: boolean;
  project_complete?: boolean;
}

export interface WorkflowMigrationSkippedArtifact {
  kind: string;
  episode: number | null;
  resource_id: string;
  artifact_path: string;
  reason: string;
  category?: "unreadable" | "legacy_audio_basis" | null;
}

export type WorkflowMigrationAdjustmentCode =
  | "history_conflict_preserved"
  | "retired_draft_trashed"
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
  | "missing_reference_preserved"
  | "placeholder_asset_added";

export interface WorkflowMigrationAdjustment {
  code: WorkflowMigrationAdjustmentCode;
  episode: number | null;
  file: string | null;
  backup: string | null;
  params: Record<string, string>;
}

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

export interface WorkflowStatus {
  schema_version: 3;
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

export interface EpisodeNextStep {
  episode: number;
  plan_stale: boolean;
  episode_complete: boolean;
  next_action: WorkflowNextAction | null;
}

export interface WorkflowPlan {
  schema_version: 3;
  status: WorkflowStatus;
  steps: WorkflowPlanStep[];
  blockers: WorkflowBlocker[];
  problems: WorkflowProblem[];
  next_action: WorkflowNextAction;
  next_alternatives: WorkflowNextAction[];
}

export interface WorkflowPlanRequest {
  episode_id?: number | null;
  confirmed_request_durations?: Record<string, number>;
}
