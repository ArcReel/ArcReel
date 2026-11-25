import { LoaderCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { TaskElapsedReadout } from "@/components/shared/TaskElapsedReadout";
import { useNowTick } from "@/hooks/useNowTick";
import { useTasksStore } from "@/stores/tasks-store";
import { formatTextTaskProgress, isTextTaskLongWait } from "@/utils/text-task-progress";
import type { TaskItem } from "@/types";

export function TextTaskLongWaitHint({
  task,
  className,
}: {
  task: TaskItem;
  className?: string;
}) {
  const { t } = useTranslation("dashboard");
  const now = useNowTick();
  if (!isTextTaskLongWait(task, now)) return null;
  return (
    <span className={cn("text-xs wrap-break-word text-muted-foreground", className)}>
      {t("text_progress_long_wait_hint")}
    </span>
  );
}

export function TextTaskProgress({
  task,
  showIcon = true,
  showElapsed = true,
  showLongWaitHint = true,
  className,
  hintClassName,
}: {
  task: TaskItem;
  showIcon?: boolean;
  showElapsed?: boolean;
  showLongWaitHint?: boolean;
  className?: string;
  hintClassName?: string;
}) {
  const { t } = useTranslation("dashboard");
  const progress = useTasksStore((s) => s.progressById.get(task.task_id));
  if (task.status !== "queued" && task.status !== "running") return null;
  const label = progress && task.status === "running"
    ? formatTextTaskProgress(progress, t)
    : t(task.status === "queued" ? "queued_status" : "generating_status");
  return (
    <span
      className={cn(
        "inline-flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-muted-foreground",
        className,
      )}
    >
      {showIcon && <LoaderCircle aria-hidden className="size-3.5 shrink-0 animate-spin" />}
      <span className="min-w-0 wrap-break-word">{label}</span>
      {showElapsed && <TaskElapsedReadout task={task} />}
      {showLongWaitHint && <TextTaskLongWaitHint task={task} className={hintClassName} />}
    </span>
  );
}
