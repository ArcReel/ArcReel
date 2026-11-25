import type { EpisodeItemRef } from "./project";


export type TaskStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";
export type TaskMediaType = "image" | "video" | "audio" | "text" | "render";

export type TaskProgress = { task_id: string; seq: number } & (
  | { phase: "waiting"; model: string }
  | { phase: "receiving"; characters: number; thinking: boolean }
  | { phase: "retrying"; error: string; attempt: number; max_attempts: number }
);

export interface TaskItem {
  progress?: TaskProgress;
  task_id: string;
  project_name: string;
  task_type: string;
  media_type: TaskMediaType;
  resource_id: string;
  episode?: number | null;
  item_id?: string | null;
  position?: number | null;
  resource_ref?: EpisodeItemRef | null;
  resource_type: string | null;
  script_file: string | null;
  payload: Record<string, unknown>;
  status: TaskStatus;
  result: Record<string, unknown> | null;
  error_message: string | null;
  error_code?: string;
  error_params?: Record<string, unknown>;
  error_detail?: string;
  cancelled_by: "user" | "cascade" | "interrupted" | null;
  provider_id: string | null;
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

export function isTerminalStatus(status: TaskStatus): boolean {
  return status === "succeeded" || status === "failed" || status === "cancelled";
}
