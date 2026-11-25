import { LoaderCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { TaskElapsedReadout } from "@/components/shared/TaskElapsedReadout";
import { useTasksStore } from "@/stores/tasks-store";
import { formatTextTaskProgress } from "@/utils/text-task-progress";
import type { TaskItem } from "@/types";

export function TextTaskProgress({ task }: { task: TaskItem }) {
  const { t } = useTranslation("dashboard");
  const progress = useTasksStore((s) => s.progressById.get(task.task_id));
  if (task.status !== "queued" && task.status !== "running") return null;
  const label = progress && task.status === "running"
    ? formatTextTaskProgress(progress, t)
    : t(task.status === "queued" ? "queued_status" : "generating_status");
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
      <LoaderCircle aria-hidden className="size-3.5 animate-spin" />
      <span>{label}</span>
      <TaskElapsedReadout task={task} />
    </span>
  );
}
