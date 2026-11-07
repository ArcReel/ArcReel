import type { EpisodesView, TaskItem } from "@/types";
import { outputTruncationOf, type OutputTruncation } from "@/utils/output-truncation";

const EPISODE_PLAN_TASK_TYPE = "text_episode_plan";
const CASCADE_FAILURE_CODE = "cascade_blocked_dependency";

/** Unplanned source volume after the planning start: portions after each file's last extracted episode, excluding unsplit gaps between extracted episodes. */
export function remainingUnits(view: EpisodesView): number {
  return view.files.reduce(
    (sum, file) =>
      sum +
      file.segments.reduce((acc, segment) => acc + (segment.kind === "unsplit" && !segment.gap ? segment.units : 0), 0),
    0,
  );
}

/** Failure reason when the previous episode-planning run failed. */
export interface PlanningFailure {
  code: string | null;
  message: string;
  /** Model whose output was truncated. */
  truncated: OutputTruncation | null;
}

function failureOf(task: TaskItem): PlanningFailure {
  return { code: task.error_code ?? null, message: task.error_message ?? "", truncated: outputTruncationOf(task) };
}

/**
 * Return this project's latest episode-planning failure reason, or null if its latest outcome was not failure.
 *
 * Window chains fail later queued windows through dependency failure; follow dependencies back to the window that actually failed.
 */
export function lastPlanningFailure(tasks: TaskItem[], projectName: string): PlanningFailure | null {
  const planning = tasks.filter((task) => task.project_name === projectName && task.task_type === EPISODE_PLAN_TASK_TYPE);
  const latest = planning.reduce<TaskItem | null>(
    (acc, task) => (acc === null || task.queued_at > acc.queued_at ? task : acc),
    null,
  );
  if (latest === null || latest.status !== "failed") return null;
  const byId = new Map(planning.map((task) => [task.task_id, task]));
  let root = latest;
  const seen = new Set<string>();
  while (root.error_code === CASCADE_FAILURE_CODE && !seen.has(root.task_id)) {
    seen.add(root.task_id);
    const dependency = root.error_params?.dependency_task_id;
    const parent = typeof dependency === "string" ? byId.get(dependency) : undefined;
    if (!parent) break;
    root = parent;
  }
  return failureOf(root);
}

/** Whole-source episode statistics: count, median volume and median reading duration. */
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
