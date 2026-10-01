import type { EpisodesView, TaskItem } from "@/types";
import { outputTruncationOf, type OutputTruncation } from "@/utils/output-truncation";

const EPISODE_PLAN_TASK_TYPE = "text_episode_plan";
const CASCADE_FAILURE_CODE = "cascade_blocked_dependency";

/** 规划起点之后还没分集的原文体量：各文件最后一个切出集之后的部分，不含夹在切出集之间的未切分原文。 */
export function remainingUnits(view: EpisodesView): number {
  return view.files.reduce(
    (sum, file) =>
      sum +
      file.segments.reduce((acc, segment) => acc + (segment.kind === "unsplit" && !segment.gap ? segment.units : 0), 0),
    0,
  );
}

/** 上一次分集规划停在失败上时的原因。 */
export interface PlanningFailure {
  code: string | null;
  message: string;
  /** 输出被截断时的模型。 */
  truncated: OutputTruncation | null;
}

function failureOf(task: TaskItem): PlanningFailure {
  return { code: task.error_code ?? null, message: task.error_message ?? "", truncated: outputTruncationOf(task) };
}

/**
 * 本项目最近一次分集规划的结局若是失败，返回失败原因；否则返回 null。
 *
 * 逐窗串联时，出错那一窗之后排着的窗口随之以「依赖任务失败」收尾，这时沿依赖找回真正出错的那一窗。
 */
export function lastPlanningFailure(tasks: TaskItem[], projectName: string): PlanningFailure | null {
  const planning = tasks.filter((task) => task.project_name === projectName && task.task_type === EPISODE_PLAN_TASK_TYPE);
  const latest = planning.reduce<TaskItem | null>(
    (acc, task) => (acc === null || task.queued_at > acc.queued_at ? task : acc),
    null,
  );
  if (latest === null || latest.status !== "failed") return null;
  let root = latest;
  const seen = new Set<string>();
  while (root.error_code === CASCADE_FAILURE_CODE && !seen.has(root.task_id)) {
    seen.add(root.task_id);
    const dependency = root.error_params?.dependency_task_id;
    const parent = planning.find((task) => task.task_id === dependency);
    if (!parent) break;
    root = parent;
  }
  return failureOf(root);
}

/** 整本源文分集后的体量统计：集数、中位体量与中位朗读时长。 */
export interface WholeSourceStats {
  count: number;
  medianUnits: number;
  medianSpokenSeconds: number | null;
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

export function wholeSourceStats(view: EpisodesView): WholeSourceStats | null {
  const placed = view.episodes.filter((episode) => episode.placed && episode.units !== null);
  if (placed.length === 0) return null;
  const spoken = placed.flatMap((episode) => (episode.spoken_seconds === null ? [] : [episode.spoken_seconds]));
  return {
    count: placed.length,
    medianUnits: Math.round(median(placed.map((episode) => episode.units ?? 0))),
    medianSpokenSeconds: spoken.length === placed.length ? median(spoken) : null,
  };
}
