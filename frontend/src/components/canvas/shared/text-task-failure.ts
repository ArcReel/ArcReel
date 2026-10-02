import { taskResourceKind } from "@/stores/tasks-store";
import type { TaskItem } from "@/types";

/** 集页上会留下失败反馈的文本任务：AI 规划脚本、编写提示词与 AI 修复。 */
export type EpisodeTextTaskKind = "text_script_plan" | "text_episode_script" | "text_draft_repair";

export interface EpisodeTextTaskFailure {
  kind: EpisodeTextTaskKind;
  task: TaskItem;
}

/** 文本任务是否属于这一集：脚本规划与提示词编写占 `episode-{N}`，AI 修复占 `episode-{N}-{doc_type}`。 */
function episodeTextTaskKind(task: TaskItem, episode: number): EpisodeTextTaskKind | null {
  const kind = taskResourceKind(task);
  const slot = `episode-${episode}`;
  if (kind === "text_script_plan" || kind === "text_episode_script") return task.resource_id === slot ? kind : null;
  if (kind === "text_draft_repair") return task.resource_id.startsWith(`${slot}-`) ? kind : null;
  return null;
}

/** 本集最近一次文本任务若以失败收尾，返回它与任务种类；最近一次没有失败或没有任务时返回 null。 */
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
