import type { NarrationSegment, ScriptOverwrite, ScriptOverwriteEntry, TaskItem } from "@/types";
import type { ReferenceUnitCapability } from "@/types/reference-video";
import type {
  WorkflowContent,
  WorkflowPlan,
  WorkflowPlanStep,
  WorkflowStatus,
} from "@/types/workflow";


/** Shared test factory for `TaskItem`. Defaults model a freshly-queued
 *  reference_video task; callers override fields relevant to each scenario. */
export function makeTask(overrides: Partial<TaskItem> = {}): TaskItem {
  return {
    task_id: "t1",
    project_name: "proj",
    task_type: "reference_video",
    media_type: "video",
    resource_id: "E1U1",
    resource_type: null,
    script_file: null,
    payload: {},
    status: "queued",
    result: null,
    error_message: null,
    cancelled_by: null,
    provider_id: null,
    provider_job_id: null,
    source: "webui",
    queued_at: "2026-04-20T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-04-20T00:00:00Z",
    ...overrides,
  };
}

/** Minimal usable plan steps; cases override only relevant dimensions. */
export function makeStep(overrides: Partial<WorkflowPlanStep> = {}): WorkflowPlanStep {
  return {
    id: "video",
    state: "ready",
    required: true,
    action: null,
    requested_ids: [],
    artifacts: {},
    problems: [],
    tasks: [],
    admission: null,
    contracts: {},
    ...overrides,
  };
}

/** Episode content states; defaults include episode sources, 2 formal script entries with completed prompts, no drafts/reminders. */
export function makeContent(overrides: Partial<WorkflowContent> = {}): WorkflowContent {
  return {
    episode_count: 1,
    whole_source: "present",
    source_remaining: false,
    ad_inputs: "not_applicable",
    products_without_selling_points: [],
    episode_source: "present",
    episode_plan_stale: false,
    expected_stale_script_plan_revision: null,
    drafts: [],
    formal_script: "present",
    script_item_count: 2,
    pending_authoring_ids: [],
    needs_replan_ids: [],
    referenced_assets_without_sheet: [],
    unregistered_references: [],
    referenced_asset_sheets_stale: [],
    referenced_assets_without_description: [],
    ...overrides,
  };
}

export function makeStatus(overrides: Partial<WorkflowStatus> = {}): WorkflowStatus {
  return {
    schema_version: 2,
    project_revision: "sha256-v1:project",
    source_revision: "sha256-v1:source",
    project: { content_mode: "narration", generation_mode: "storyboard", grid_storyboard: false },
    target: { episode: 1, script: "scripts/episode_1.json", script_filename: "episode_1.json", source: "source/episode_1.txt" },
    blockers: [],
    issues: [],
    content: makeContent(),
    operations: {},
    gates: {},
    artifacts: {},
    next_action: {
      type: "generate_videos",
      args: {},
      requested_ids: [],
      requires_confirmation: false,
      reason: "next",
    },
    next_alternatives: [],
    ...overrides,
  };
}

export function makePlan(overrides: Partial<WorkflowPlan> = {}): WorkflowPlan {
  const status = overrides.status ?? makeStatus();
  return {
    schema_version: 2,
    status,
    steps: [makeStep()],
    blockers: status.blockers,
    problems: [],
    next_action: status.next_action,
    next_alternatives: status.next_alternatives,
    ...overrides,
  };
}

/** Narration storyboards default to text prompts on both sides; cases override structured prompts or pending null as needed. */
export function makeNarrationSegment(overrides: Partial<NarrationSegment> = {}): NarrationSegment {
  return {
    segment_id: "E1S01",
    episode: 1,
    duration_seconds: 8,
    segment_break: false,
    novel_text: "旁白正文",
    characters_in_segment: [],
    scenes: [],
    props: [],
    image_prompt: "雨夜街道",
    video_prompt: "撑伞走过",
    ...overrides,
  };
}

/** Shared server per-unit bucket-result builder; defaults to reference-free i2v units, overridden by scenarios. */
export function makeReferenceUnitCapability(
  unitId: string,
  overrides: Partial<ReferenceUnitCapability> = {},
): ReferenceUnitCapability {
  return {
    unit_id: unitId,
    declared_capability: "i2v",
    hydrated_capability: "i2v",
    declared_references: [],
    unavailable_references: [],
    unregistered_references: [],
    allowed_durations: [3, 8],
    excluded_durations: {},
    duration_endpoint_fixed: false,
    duration_endpoint_fixed_reason: null,
    problem: null,
    problems: [],
    ...overrides,
  };
}

/** Shared overwrite-manifest entry builder; defaults to entries owning no artifacts. */
export function makeScriptOverwriteEntry(
  id: string,
  overrides: Partial<ScriptOverwriteEntry> = {},
): ScriptOverwriteEntry {
  return {
    id,
    has_storyboard: false,
    has_video: false,
    has_narration_audio: false,
    has_end_frame: false,
    grid_id: null,
    ...overrides,
  };
}

/** Shared overwrite-manifest builder; `text` is server-rendered loss manifests, displayed unchanged in confirmations. */
export function makeScriptOverwrite(overrides: Partial<ScriptOverwrite> = {}): ScriptOverwrite {
  return {
    revision: "sha256-v1:formal",
    entries: [],
    storyboard_count: 0,
    video_count: 0,
    narration_audio_count: 0,
    end_frame_count: 0,
    grid_member_count: 0,
    grid_count: 0,
    text: "",
    ...overrides,
  };
}
