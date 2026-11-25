import type { TFunction } from "i18next";
import type { TaskItem, TaskProgress } from "@/types";
import { taskElapsed } from "@/utils/task-elapsed";

export const TEXT_TASK_LONG_WAIT_THRESHOLD_MS = 15_000;
export const LONG_EPISODE_SOURCE_THRESHOLD = 15_000;

export function isTextTask(task: Pick<TaskItem, "media_type" | "task_type">): boolean {
  return task.media_type === "text" || task.task_type.startsWith("text_");
}

export function isTextTaskLongWait(
  task: Pick<TaskItem, "status" | "queued_at" | "started_at" | "finished_at">,
  now: number,
): boolean {
  if (task.status !== "queued" && task.status !== "running") return false;
  const elapsed = taskElapsed(task, now);
  return elapsed !== null && elapsed.ms > TEXT_TASK_LONG_WAIT_THRESHOLD_MS;
}

export function episodeSourceCharCount(text: string): number {
  return [...text].length;
}

export function formatTextTaskProgress(progress: TaskProgress, t: TFunction<"dashboard">): string {
  switch (progress.phase) {
    case "waiting":
      return t("text_progress_waiting", { model: progress.model });
    case "receiving":
      return progress.thinking
        ? t("text_progress_thinking")
        : t("text_progress_receiving", { characters: progress.characters });
    case "retrying":
      return t("text_progress_retrying", { attempt: progress.attempt, max: progress.max_attempts });
  }
}
