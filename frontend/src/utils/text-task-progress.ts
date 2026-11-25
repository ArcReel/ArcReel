import type { TFunction } from "i18next";
import type { TaskProgress } from "@/types";

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
