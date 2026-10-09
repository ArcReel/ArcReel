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
/** `render` 是本地渲染车道（成片等），不产生用量记录。 */
export type TaskMediaType = "image" | "video" | "audio" | "render";

export interface TaskItem {
  task_id: string;
  project_name: string;
  task_type: string;
  media_type: TaskMediaType;
  resource_id: string;
  /** resource_id 所属集的标题与播出位置；ID 不带集前缀或集已不在账本里时为 null。 */
  resource_ref?: EpisodeItemRef | null;
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
  /** Present when error_message was stored as a machine-encoded task failure. */
  error_code?: string;
  error_params?: Record<string, unknown>;
  /** 服务端原文；只在 error_message 已换成本地化文案时出现，未知错误的原文就在 error_message 里。 */
  error_detail?: string;
  cancelled_by: "user" | "cascade" | "interrupted" | null;
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
  succeeded: number;
  failed: number;
  cancelled: number;
  total: number;
}

/** 终态：任务生命周期末端，不再占用 resource。 */
export function isTerminalStatus(status: TaskStatus): boolean {
  return status === "succeeded" || status === "failed" || status === "cancelled";
}
