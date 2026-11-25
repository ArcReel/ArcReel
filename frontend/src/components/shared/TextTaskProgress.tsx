import { LoaderCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { TaskElapsedReadout } from "@/components/shared/TaskElapsedReadout";
import { useNowTick } from "@/hooks/useNowTick";
import { useTasksStore } from "@/stores/tasks-store";
import { formatTextTaskProgress, isTextTaskLongWait } from "@/utils/text-task-progress";
import type { TaskItem, TaskProgress } from "@/types";

export function TextTaskLongWaitHint({
  task,
  className,
}: {
  task: TaskItem;
  className?: string;
}) {
  const { t } = useTranslation("dashboard");
  const now = useNowTick();
  const showHint = isTextTaskLongWait(task, now);
  return (
    <span aria-live="polite" className={showHint ? cn("text-xs wrap-break-word text-muted-foreground", className) : "sr-only"}>
      {showHint && t("text_progress_long_wait_hint")}
    </span>
  );
}

export function TextTaskPhaseLabel({
  progress,
  fallback,
  className,
}: {
  progress?: TaskProgress;
  fallback: string;
  className: string;
}) {
  const { t } = useTranslation("dashboard");
  const counting = progress?.phase === "receiving" && !progress.thinking;
  const label = progress ? formatTextTaskProgress(progress, t) : fallback;
  return (
    <>
      <span role="status" className={counting ? "sr-only" : className}>
        {counting ? t("text_progress_receiving_phase") : label}
      </span>
      {counting && <span className={className}>{label}</span>}
    </>
  );
}

export function TextTaskProgress({
  task,
  showIcon = true,
  showLongWaitHint = true,
  className,
}: {
  task: TaskItem;
  showIcon?: boolean;
  showLongWaitHint?: boolean;
  className?: string;
}) {
  const { t } = useTranslation("dashboard");
  const progress = useTasksStore((s) => s.progressById.get(task.task_id));
  if (task.status !== "queued" && task.status !== "running") return null;
  return (
    <span
      className={cn(
        "inline-flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-muted-foreground",
        className,
      )}
    >
      {showIcon && <LoaderCircle aria-hidden className="size-3.5 shrink-0 animate-spin" />}
      <TextTaskPhaseLabel
        progress={task.status === "running" ? progress : undefined}
        fallback={t(task.status === "queued" ? "queued_status" : "generating_status")}
        className="min-w-0 wrap-break-word"
      />
      <TaskElapsedReadout task={task} />
      {showLongWaitHint && <TextTaskLongWaitHint task={task} />}
    </span>
  );
}
