import { useId, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import type { WorkflowTaskObservation } from "@/types/workflow";
import { useTaskRowsByIds } from "@/stores/tasks-store";
import { TaskElapsedReadout } from "@/components/shared/TaskElapsedReadout";
import { CheckpointNote } from "./CheckpointNote";
import { UnitTag } from "./UnitTag";
import { taskToneClass } from "./state-language";

/**
 * 这一步上正在跑的尝试：排队中与运行中的任务。它们是进度，不是问题。
 *
 * 芯片是**描边**的，产物计量条是**填充**的——形状上就说清楚「一次尝试」和「一件东西」
 * 不是同一类事物。恢复中的任务停在这条轴上：它还没有产出任何可用文件，把它画成 current
 * 产物会让用户以为已经生成好了。
 *
 * 供应商收单交给 {@link CheckpointNote} 在列表后单独说一次，理由见该组件。
 */
export function TaskChips({ tasks }: { tasks: WorkflowTaskObservation[] }) {
  const { t } = useTranslation("workflow");
  const headingId = useId();
  const taskIds = useMemo(() => tasks.map((task) => task.task_id), [tasks]);
  // 工作流观测不带时间戳，时长按 task_id 回查任务队列。
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
              <UnitTag unitId={task.unit_id} />
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
