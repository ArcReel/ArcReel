import type {
  CallType,
  TaskItem,
  UsageRecord,
  UsageRecordStatus,
} from "@/types";
import { parseIsoTimestamp } from "@/utils/date-format";

/**
 * 记录行的视图模型。已结束的调用与尚未产生调用行的进行中任务都投影到这里，
 * 让记录表、进行中子块与顶栏悬浮层共用同一个行组件。
 */
export interface UsageRecordView {
  /** React key；进行中的任务行没有记录 id，用任务 id 组键。 */
  key: string;
  /** 可打开详情的行才有记录 id；进行中的任务行为 null。 */
  recordId: number | null;
  /** 服务了任务的调用带任务 id；无任务的文本调用与端点试跑为 null。 */
  taskId: string | null;
  /** 端点试跑记录为空串。 */
  projectName: string;
  mediaType: CallType;
  provider: string | null;
  /** 排队中的任务模型尚未解析时为 null，界面显示「待解析」。 */
  model: string | null;
  status: UsageRecordStatus;
  purpose: string | null;
  segmentId: string | null;
  errorCode: string | null;
  errorMessage: string | null;
  /** 已结束调用的开始时刻；进行中行用它排序与计时。 */
  startedAt: string;
  finishedAt: string | null;
  durationMs: number | null;
  costAmount: number;
  currency: string;
}

export function usageRecordToView(record: UsageRecord): UsageRecordView {
  return {
    key: `record:${record.id}`,
    recordId: record.id,
    taskId: record.task_id,
    projectName: record.project_name,
    mediaType: record.media_type,
    provider: record.provider,
    model: record.model || null,
    status: record.status,
    purpose: record.purpose,
    segmentId: record.segment_id,
    errorCode: record.error_code,
    errorMessage: record.error_message,
    startedAt: record.started_at,
    finishedAt: record.finished_at,
    durationMs: record.duration_ms,
    costAmount: record.cost_amount,
    currency: record.currency,
  };
}

/**
 * 进行中的任务投影成记录行。任务侧没有模型与调用行，模型留空由界面显示「待解析」；
 * 计时起点取 `started_at`，尚未开始时取 `queued_at`。
 */
export function taskToUsageRecordView(task: TaskItem): UsageRecordView {
  return {
    key: `task:${task.task_id}`,
    recordId: null,
    taskId: task.task_id,
    projectName: task.project_name,
    mediaType: task.media_type,
    provider: task.provider_id,
    model: null,
    status: "pending",
    purpose: "generation_task",
    segmentId: task.resource_id || null,
    errorCode: null,
    errorMessage: null,
    startedAt: task.started_at ?? task.queued_at,
    finishedAt: null,
    durationMs: null,
    costAmount: 0,
    currency: "USD",
  };
}

/**
 * 进行中区按开始时刻倒序：最近排队或启动的排在最前。任务与调用两侧的时间戳未必带
 * 同样的时区后缀，故按解析出的时刻比较，不按字符串。
 */
export function sortByStartedDesc(views: UsageRecordView[]): UsageRecordView[] {
  return [...views].sort(
    (a, b) =>
      parseIsoTimestamp(b.startedAt).getTime() - parseIsoTimestamp(a.startedAt).getTime(),
  );
}
