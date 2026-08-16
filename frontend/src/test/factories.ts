import type { TaskItem } from "@/types";
import type {
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

/** 一个最小可用的计划步骤；各用例只覆盖自己关心的轴。 */
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
    generation_result: null,
    contracts: {},
    ...overrides,
  };
}

export function makeStatus(overrides: Partial<WorkflowStatus> = {}): WorkflowStatus {
  return {
    schema_version: 1,
    project_revision: "sha256-v1:project",
    source_revision: "sha256-v1:source",
    project: { content_mode: "narration", generation_mode: "storyboard", grid_storyboard: false },
    target: { episode: 1, script: "scripts/episode_1.json", script_filename: "episode_1.json", source: "source/episode_1.txt" },
    state: "VIDEO",
    blockers: [],
    gates: {},
    artifacts: {},
    next_action: {
      type: "generate_videos",
      args: {},
      requested_ids: [],
      requires_confirmation: false,
      reason: "next",
    },
    ...overrides,
  };
}

export function makePlan(overrides: Partial<WorkflowPlan> = {}): WorkflowPlan {
  const status = overrides.status ?? makeStatus();
  return {
    schema_version: 1,
    status,
    narration_delivery: {
      selected: null,
      options: ["post_production", "use_tts"],
      persisted: false,
    },
    steps: [makeStep()],
    blockers: status.blockers,
    problems: [],
    next_action: status.next_action,
    ...overrides,
  };
}
