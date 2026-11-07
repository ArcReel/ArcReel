import { taskResourceKind } from "@/stores/tasks-store";
import type { TaskItem } from "@/types";

/** Text tasks leaving failure feedback on episode pages: AI script planning, prompt authoring and AI repair. */
export type EpisodeTextTaskKind = "text_script_plan" | "text_episode_script" | "text_draft_repair";

export interface EpisodeTextTaskFailure {
  kind: EpisodeTextTaskKind;
  task: TaskItem;
}

/** Whether a text task belongs to this episode: script planning/prompt authoring use `episode-{N}`; AI repair uses `episode-{N}-{doc_type}`. */
function episodeTextTaskKind(task: TaskItem, episode: number): EpisodeTextTaskKind | null {
  const kind = taskResourceKind(task);
  const slot = `episode-${episode}`;
  if (kind === "text_script_plan" || kind === "text_episode_script") return task.resource_id === slot ? kind : null;
  if (kind === "text_draft_repair") return task.resource_id.startsWith(`${slot}-`) ? kind : null;
  return null;
}

/** Return the latest episode text task and its kind only if it failed; return null if it did not fail or no task exists. */
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
