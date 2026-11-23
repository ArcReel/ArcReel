import type {
  CallType,
  EpisodeItemRef,
  TaskItem,
  TaskMediaType,
  UsageRecord,
  UsageRecordStatus,
} from "@/types";
import { parseIsoTimestamp } from "@/utils/date-format";

export interface UsageRecordView {
  key: string;
  recordId: number | null;
  taskId: string | null;
  projectName: string;
  projectTitle: string | null;
  mediaType: CallType;
  provider: string | null;
  model: string | null;
  status: UsageRecordStatus;
  purpose: string | null;
  segmentId: string | null;
  segmentRef: EpisodeItemRef | null;
  errorCode: string | null;
  errorMessage: string | null;
  startedAt: string;
  finishedAt: string | null;
  durationMs: number | null;
  costAmount: number | null;
  unpricedReason: string | null;
  currency: string;
}

export function usageRecordToView(record: UsageRecord): UsageRecordView {
  return {
    key: `record:${record.id}`,
    recordId: record.id,
    taskId: record.task_id,
    projectName: record.project_name,
    projectTitle: record.project_title ?? null,
    mediaType: record.media_type,
    provider: record.provider,
    model: record.model || null,
    status: record.status,
    purpose: record.purpose,
    segmentId: record.segment_id,
    segmentRef: record.segment_ref ?? null,
    errorCode: record.error_code,
    errorMessage: record.error_message,
    startedAt: record.started_at,
    finishedAt: record.finished_at,
    durationMs: record.duration_ms,
    costAmount: record.cost_amount,
    unpricedReason: record.unpriced_reason,
    currency: record.currency,
  };
}

const RESOURCE_TYPE_BY_KIND: Record<string, string> = {
  storyboard: "storyboards",
  grid: "grids",
  video: "videos",
  reference_video: "reference_videos",
  tts: "audio",
  voice_sample: "audio",
  character: "characters",
  character_derivative: "character_derivatives",
  scene: "scenes",
  prop: "props",
  product: "products",
};

const SEGMENT_RESOURCE_TYPES: Record<TaskMediaType, ReadonlySet<string> | null> = {
  image: new Set(["storyboards", "videos", "grids"]),
  video: new Set(["storyboards", "videos", "reference_videos"]),
  audio: null,
  render: new Set(),
};

export function taskSegmentId(task: TaskItem): string | null {
  if (!task.resource_id) return null;
  const allowed = SEGMENT_RESOURCE_TYPES[task.media_type];
  if (allowed === null) return task.resource_id;
  const kind = task.task_type === "image_edit" ? task.resource_type : task.task_type;
  const resourceType = kind === null ? undefined : RESOURCE_TYPE_BY_KIND[kind];
  if (resourceType === undefined) return null;
  return allowed.has(resourceType) ? task.resource_id : null;
}

export function taskToUsageRecordView(task: TaskItem): UsageRecordView | null {
  if (task.media_type === "render") return null;
  return {
    key: `task:${task.task_id}`,
    recordId: null,
    taskId: task.task_id,
    projectName: task.project_name,
    projectTitle: null,
    mediaType: task.media_type,
    provider: task.provider_id,
    model: task.model ?? null,
    status: "pending",
    purpose: "generation_task",
    segmentId: taskSegmentId(task),
    segmentRef: taskSegmentId(task) === null ? null : (task.resource_ref ?? null),
    errorCode: null,
    errorMessage: null,
    startedAt: task.started_at ?? task.queued_at,
    finishedAt: null,
    durationMs: null,
    costAmount: 0,
    unpricedReason: null,
    currency: "USD",
  };
}

export function sortByStartedDesc(views: UsageRecordView[]): UsageRecordView[] {
  return [...views].sort(
    (a, b) =>
      parseIsoTimestamp(b.startedAt).getTime() - parseIsoTimestamp(a.startedAt).getTime(),
  );
}
