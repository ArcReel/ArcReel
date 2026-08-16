import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { ProviderCheckpoint, WorkflowTaskObservation } from "@/types/workflow";
import { UnitTag } from "./UnitTag";
import { taskTone } from "./state-language";

const ACTIVE_STATUSES = new Set(["queued", "running", "cancelling"]);

function CheckpointMark({ checkpoint }: { checkpoint: ProviderCheckpoint }) {
  const { t } = useTranslation("workflow");
  if (!checkpoint.submitted) return null;
  const jobId = checkpoint.provider_job_id;
  return (
    <span
      className="flex flex-wrap items-baseline gap-x-1 text-[11px]"
      style={{ color: "var(--color-text-3)" }}
    >
      <span>{t("checkpoint_submitted", { provider: checkpoint.provider_id ?? t("checkpoint_provider_unknown") })}</span>
      {jobId && (
        <code translate="no" className="break-all font-mono">
          {jobId}
        </code>
      )}
    </span>
  );
}

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
 * provider checkpoint 单独一行，因为它回答的是另一个问题：供应商侧是否已经收单。已收单时
 * 重试可能重复计费，这件事必须自己占一行，不能被折进任务状态词里。
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
                <CheckpointMark checkpoint={task.provider_checkpoint} />
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
