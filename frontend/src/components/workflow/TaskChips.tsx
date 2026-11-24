import { useId, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import type { WorkflowTaskObservation } from "@/types/workflow";
import { useTaskRowsByIds } from "@/stores/tasks-store";
import { TaskElapsedReadout } from "@/components/shared/TaskElapsedReadout";
import { CheckpointNote } from "./CheckpointNote";
import { EpisodeItemRef } from "@/components/shared/episode-item/EpisodeItemRef";
import { taskToneClass } from "./state-language";

export function TaskChips({ tasks }: { tasks: WorkflowTaskObservation[] }) {
  const { t } = useTranslation("workflow");
  const headingId = useId();
  const taskIds = useMemo(() => tasks.map((task) => task.task_id), [tasks]);
  const taskRows = useTaskRowsByIds(taskIds);
  if (tasks.length === 0) return null;
  const submitted = tasks.filter((task) => task.provider_checkpoint?.submitted).map((task) => task.unit_id);

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-1">
      <h4 id={headingId} className="text-xs text-muted-foreground">
        {t("tasks_title", { count: tasks.length })}
      </h4>
      <ul className="flex flex-col gap-1">
        {tasks.map((task) => {
          const row = taskRows.get(task.task_id);
          return (
            <li key={task.task_id} className="flex flex-wrap items-center gap-1.5">
              <EpisodeItemRef itemId={task.unit_id} />
              <span className={cn("rounded-full border px-2 py-0.5 text-xs", taskToneClass(task.status))}>
                {t(`task_type_${task.task_type}`, { defaultValue: t("task_fallback_type") })}
                {" · "}
                {t(`task_status_${task.status}`, { defaultValue: t("task_status_unknown") })}
              </span>
              {row && <TaskElapsedReadout task={row} className="text-xs text-muted-foreground" />}
            </li>
          );
        })}
      </ul>
      <CheckpointNote unitIds={submitted} />
    </section>
  );
}
