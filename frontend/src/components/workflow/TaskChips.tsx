import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { WorkflowTaskObservation } from "@/types/workflow";
import { CheckpointNote } from "./CheckpointNote";
import { UnitTag } from "./UnitTag";
import { taskTone } from "./state-language";

const ACTIVE_STATUSES = new Set(["queued", "running", "cancelling"]);

interface Props {
  tasks: WorkflowTaskObservation[];
}

/**
 * 这一步上正在发生的尝试。
 *
 * 芯片是**描边**的，产物计量条是**填充**的——形状上就说清楚「一次尝试」和「一件东西」
 * 不是同一类事物。恢复中的任务停在这条轴上：它还没有产出任何可用文件，把它画成 current
 * 产物会让用户以为已经生成好了。
 *
 * 供应商 checkpoint 交给 {@link CheckpointNote} 单独成行，理由见该组件。
 */
export function TaskChips({ tasks }: Props) {
  const { t } = useTranslation("workflow");
  const headingId = useId();
  if (tasks.length === 0) return null;
  const activeCount = tasks.filter((task) => ACTIVE_STATUSES.has(task.status)).length;

  return (
    <section aria-labelledby={headingId} className="space-y-1">
      <h4 id={headingId} className="text-[11.5px]" style={{ color: "var(--color-text-4)" }}>
        {t("tasks_title", { count: activeCount })}
      </h4>
      <ul className="flex flex-col gap-1">
        {tasks.map((task) => {
          const tone = taskTone(task.status);
          return (
            <li key={task.task_id} className="flex flex-col gap-0.5">
              <span className="flex flex-wrap items-center gap-1.5">
                <UnitTag unitId={task.unit_id} />
                <span
                  className="rounded-full px-2 py-0.5 text-[11px]"
                  style={{ border: `1px solid ${tone.ring}`, color: tone.color }}
                >
                  {t(`task_type_${task.task_type}`, { defaultValue: task.task_type })}
                  {" · "}
                  {t(`task_status_${task.status}`, { defaultValue: task.status })}
                </span>
              </span>
              {task.provider_checkpoint && (
                <CheckpointNote checkpoint={task.provider_checkpoint} showJobId />
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
