/**
 * Task queue type definitions.
 *
 * Maps to backend models in:
 * - lib/generation_queue.py (GenerationQueue task schema, get_task_stats)
 * - webui/server/routers/tasks.py (API responses)
 */

export type TaskStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "succeeded"
  | "failed"
  | "cancelled";
export type TaskMediaType = "image" | "video" | "audio";

export interface TaskItem {
  task_id: string;
  project_name: string;
  task_type: string;
  media_type: TaskMediaType;
  resource_id: string;
  /**
   * 资源种类。仅 image_edit 任务写入（character/scene/prop/product/storyboard）——
   * 其余任务类型 task_type 本身已按资源种类区分，故为 null。占用匹配据此把编辑任务
   * 归入对应资源槽（见 tasks-store 的 taskResourceKind）。
   */
  resource_type: string | null;
  script_file: string | null;
  /** Parsed from payload_json in the SQLite row */
  payload: Record<string, unknown>;
  status: TaskStatus;
  result: Record<string, unknown> | null;
  error_message: string | null;
  cancelled_by: "user" | "cascade" | null;
  provider_id: string | null;
  provider_job_id: string | null;
  source: "webui" | "agent";
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface TaskStats {
  queued: number;
  running: number;
  cancelling: number;
  succeeded: number;
  failed: number;
  cancelled: number;
  total: number;
}
