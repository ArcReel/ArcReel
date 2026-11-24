import { taskResourceKind } from "@/stores/tasks-store";
import type { TaskItem } from "@/types";

export type EpisodeTextTaskKind = "text_script_plan" | "text_episode_script";

export interface EpisodeTextTaskFailure {
  kind: EpisodeTextTaskKind;
  task: TaskItem;
}

function episodeTextTaskKind(task: TaskItem, episode: number): EpisodeTextTaskKind | null {
  const kind = taskResourceKind(task);
  const slot = `episode-${episode}`;
  if (kind === "text_script_plan" || kind === "text_episode_script") return task.resource_id === slot ? kind : null;
  return null;
}

export function latestEpisodeTextTaskFailure(
  tasks: readonly TaskItem[],
  projectName: string,
  episode: number,
): EpisodeTextTaskFailure | null {
  let latest: EpisodeTextTaskFailure | null = null;
  for (const task of tasks) {
    if (task.project_name !== projectName) continue;
    const kind = episodeTextTaskKind(task, episode);
    if (kind === null) continue;
    if (latest === null || task.queued_at > latest.task.queued_at) latest = { kind, task };
  }
  return latest?.task.status === "failed" ? latest : null;
}
