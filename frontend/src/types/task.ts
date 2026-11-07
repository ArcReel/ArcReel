import type { EpisodeItemRef } from "./project";

/**
 * Task queue type definitions.
 *
 * Maps to backend models in:
 * - lib/generation/generation_queue.py (GenerationQueue task schema, get_task_stats)
 * - webui/server/routers/tasks.py (API responses)
 */

export type TaskStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";
/** `render` is the local rendering lane (final cuts, etc.) and produces no usage records. */
export type TaskMediaType = "image" | "video" | "audio" | "render";

export interface TaskItem {
  task_id: string;
  project_name: string;
  task_type: string;
  media_type: TaskMediaType;
  resource_id: string;
  /** Title and broadcast position of the episode containing resource_id; null when the ID has no episode prefix or the episode is no longer in the ledger. */
  resource_ref?: EpisodeItemRef | null;
  /**
   * Resource kind, written only for image_edit tasks (character/scene/prop/product/storyboard).
   * For other task types, task_type already distinguishes the resource kind, so this is null. Occupancy matching uses this to assign editing tasks
   * to the corresponding resource slot (see taskResourceKind in tasks-store).
   */
  resource_type: string | null;
  script_file: string | null;
  /** Parsed from payload_json in the SQLite row */
  payload: Record<string, unknown>;
  status: TaskStatus;
  result: Record<string, unknown> | null;
  error_message: string | null;
  /** Present when error_message was stored as a machine-encoded task failure. */
  error_code?: string;
  error_params?: Record<string, unknown>;
  /** Original server text, present only when error_message has been replaced with localized wording; original text for unknown errors stays in error_message. */
  error_detail?: string;
  cancelled_by: "user" | "cascade" | "interrupted" | null;
  provider_id: string | null;
  /** Model used by the latest call of a running task; null while queued or before a call has been made. Returned only by the task list endpoint. */
  model?: string | null;
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
  succeeded: number;
  failed: number;
  cancelled: number;
  total: number;
}

/** Terminal state: the end of the task lifecycle, no longer occupying the resource. */
export function isTerminalStatus(status: TaskStatus): boolean {
  return status === "succeeded" || status === "failed" || status === "cancelled";
}
