import type { TaskItem } from "@/types";

/** Shared test factory for `TaskItem`. Defaults model a freshly-queued
 *  reference_video task; callers override fields relevant to each scenario. */
export function makeTask(overrides: Partial<TaskItem> = {}): TaskItem {
  return {
    task_id: "t1",
    project_name: "proj",
    task_type: "reference_video",
    media_type: "video",
    resource_id: "E1U1",
    script_file: null,
    payload: {},
    status: "queued",
    result: null,
    error_message: null,
    cancelled_by: null,
    source: "webui",
    queued_at: "2026-04-20T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-04-20T00:00:00Z",
    ...overrides,
  };
}
