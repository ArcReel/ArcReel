import type {
  CallType,
  EpisodeItemRef,
  TaskItem,
  TaskMediaType,
  UsageRecord,
  UsageRecordStatus,
} from "@/types";
import { parseIsoTimestamp } from "@/utils/date-format";

/**
 * Record-row view models project both completed calls and running tasks without call rows,
 * sharing row components across tables/running subsections/header popovers.
 */
export interface UsageRecordView {
  /** React keys; running tasks lack record IDs and use task-ID-derived keys. */
  key: string;
  /** Only detail-openable rows have record IDs; running tasks use null. */
  recordId: number | null;
  /** Task-backed calls carry task IDs; taskless text calls/endpoint trials use null. */
  taskId: string | null;
  /** Endpoint trials use empty strings. */
  projectName: string;
  /** Project titles, falling back to names in UI; running tasks lack titles. */
  projectTitle: string | null;
  mediaType: CallType;
  provider: string | null;
  /** Queued unresolved models are null, displayed as Pending resolution. */
  model: string | null;
  status: UsageRecordStatus;
  purpose: string | null;
  segmentId: string | null;
  /** segmentId owning episode titles/broadcast positions, displayed as Title · S01. */
  segmentRef: EpisodeItemRef | null;
  errorCode: string | null;
  errorMessage: string | null;
  /** Start times of completed calls; running rows use them for sorting/timing. */
  startedAt: string;
  finishedAt: string | null;
  durationMs: number | null;
  /** null means unpriced, 0 means zero-priced. */
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

/**
 * Task backend resource types. Accounting always uses plural buckets; singular `image_edit` `resource_type` values also
 * convert during execution into the same buckets (`server/services/tasks/image_edit_tasks.py`
 * `edit_version_resource_type`), so both sources share this table.
 */
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

/**
 * Resource types usable as `segment_id`, matching backend `lib/generation/media_generator.py`
 * `segment_id_for` allowlists; audio has no allowlist and passes through unconditionally.
 */
const SEGMENT_RESOURCE_TYPES: Record<TaskMediaType, ReadonlySet<string> | null> = {
  image: new Set(["storyboards", "videos", "grids"]),
  video: new Set(["storyboards", "videos", "reference_videos"]),
  audio: null,
  render: new Set(),
};

/**
 * Storyboard labels for running tasks. Persisted completed records derive `segment_id` through backend `segment_id_for`;
 * running rows use identical allowlists here, preventing character/prop/scene image tasks from showing
 * Storyboard <name> while running and generic targets after completion.
 */
export function taskSegmentId(task: TaskItem): string | null {
  if (!task.resource_id) return null;
  // audio accounting ignores resource_type; registration of task resource kinds in this branch never changes conclusions.
  const allowed = SEGMENT_RESOURCE_TYPES[task.media_type];
  if (allowed === null) return task.resource_id;
  const kind = task.task_type === "image_edit" ? task.resource_type : task.task_type;
  const resourceType = kind === null ? undefined : RESOURCE_TYPE_BY_KIND[kind];
  if (resourceType === undefined) return null;
  return allowed.has(resourceType) ? task.resource_id : null;
}

/**
 * Project running tasks into rows. Tasks lack models/call rows, leaving models empty for Pending resolution;
 * timing starts at `started_at`, or `queued_at` before starting. Local rendering tasks (`render`) call no
 * providers, record no usage and have no rows; return null.
 */
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

/**
 * Running regions sort newest start first, including recently queued/started tasks. Task/call timestamps may use different
 * timezone suffixes, so compare parsed instants rather than strings.
 */
export function sortByStartedDesc(views: UsageRecordView[]): UsageRecordView[] {
  return [...views].sort(
    (a, b) =>
      parseIsoTimestamp(b.startedAt).getTime() - parseIsoTimestamp(a.startedAt).getTime(),
  );
}
