import { useState } from "react";
import { AlertTriangle, Loader2, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { TaskItem } from "@/types";
import { useNowTick } from "@/hooks/useNowTick";
import { MEDIA_META, elapsedSince, purposeKey } from "./usage-record-format";
import type { UsageRecordView } from "./usage-record-view";
import { taskWarnings } from "./usage-record-view";

interface UsageActiveRowProps {
  view: UsageRecordView;
  /** 有任务代表的行才可取消、才可能带生成警示；无任务的 pending 调用为 null。 */
  task: TaskItem | null;
  /** 供应商 id → 显示名，查不到回退 id。 */
  providerLabel: (provider: string | null) => string;
  onCancel?: (taskId: string) => void;
  cancelling?: boolean;
}

const TASK_STATUS_KEYS: Record<TaskItem["status"], string> = {
  running: "generating_status",
  queued: "queued_status",
  cancelling: "cancelling_status",
  succeeded: "completed_status",
  failed: "failed_status",
  cancelled: "cancelled_status",
};

/** 运行中的不确定进度条：没有真实百分比，只标「还在动」。 */
function ProgressPulse() {
  return (
    <div
      aria-hidden="true"
      className="mt-1 h-0.5 w-full overflow-hidden rounded-full"
      style={{ background: "oklch(0.16 0.010 265 / 0.7)" }}
    >
      <div className="animate-progress-pulse h-full w-1/3 rounded-full bg-accent" />
    </div>
  );
}

/**
 * 进行中区的一行。左栏同时容纳两种来源：任务 store 里项目内进行中的任务，以及没有
 * 任务代表的 pending 调用（剧本生成、助手会话一类）。后者不可取消，也没有警示。
 */
export function UsageActiveRow({
  view,
  task,
  providerLabel,
  onCancel,
  cancelling,
}: UsageActiveRowProps) {
  const { t } = useTranslation("dashboard");
  const [warningsOpen, setWarningsOpen] = useState(false);
  const now = useNowTick();

  const media = MEDIA_META[view.mediaType];
  const MediaIcon = media.Icon;
  const warnings = task ? taskWarnings(task) : [];
  const running = task?.status === "running" || task?.status === "cancelling";
  const purpose = purposeKey(view.purpose);
  const target = view.segmentId
    ? t("usage_target_segment", { id: view.segmentId })
    : purpose
      ? t(purpose)
      : "—";
  const statusText = task
    ? (task.error_message ?? t(TASK_STATUS_KEYS[task.status]))
    : t("usage_status_pending");
  const detailId = `usage-active-warnings-${view.key}`;

  return (
    <div className="px-2 py-1.5">
      <div className="flex items-start gap-2">
        <MediaIcon
          aria-label={t(media.labelKey)}
          className="mt-[3px] h-3.5 w-3.5 shrink-0"
          style={{ color: media.color }}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 text-[11.5px] text-text-2">
            <span className="truncate">{target}</span>
            {warnings.length > 0 && (
              <button
                type="button"
                aria-expanded={warningsOpen}
                aria-controls={detailId}
                onClick={() => setWarningsOpen((open) => !open)}
                className="focus-ring inline-flex shrink-0 items-center gap-0.5 rounded px-0.5 text-[10.5px] text-warn"
                title={t("task_warnings_hint")}
              >
                <AlertTriangle aria-hidden="true" className="h-3 w-3" />
                <span className="num">{warnings.length}</span>
                <span className="sr-only">
                  {t("task_warnings_count", { count: warnings.length })}
                </span>
              </button>
            )}
            <span
              aria-hidden="true"
              className={
                "ml-auto h-[5px] w-[5px] shrink-0 rounded-full bg-accent-2" +
                (running ? " animate-breathe" : "")
              }
            />
            <span className="num shrink-0 text-[11px] text-text-3">
              {elapsedSince(view.startedAt, now)}
            </span>
            {task && onCancel && (
              <button
                type="button"
                disabled={cancelling}
                onClick={() => onCancel(task.task_id)}
                className="focus-ring shrink-0 rounded p-0.5 text-text-4 transition-colors hover:text-danger-2 disabled:opacity-60"
                aria-label={t(cancelling ? "cancelling_status" : "cancel_this_task")}
                title={
                  cancelling
                    ? t("cancelling_status")
                    : task.status === "running"
                      ? t("cancel_running_warning")
                      : t("cancel_task")
                }
              >
                {cancelling ? (
                  <Loader2 aria-hidden="true" className="h-3 w-3 animate-spin" />
                ) : (
                  <X aria-hidden="true" className="h-3 w-3" />
                )}
              </button>
            )}
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-[10.5px] text-text-3">
            <span className="truncate">
              {providerLabel(view.provider)} · {view.model ?? t("usage_model_unresolved")}
            </span>
            <span className="ml-auto shrink-0 truncate">{statusText}</span>
          </div>
          {running && <ProgressPulse />}
        </div>
      </div>
      {warningsOpen && warnings.length > 0 && (
        <ul
          id={detailId}
          className="mt-1 space-y-1 rounded px-2 py-1.5 text-[10.5px]"
          style={{
            background: "oklch(0.35 0.10 70 / 0.10)",
            color: "oklch(0.86 0.09 70)",
            border: "1px solid oklch(0.50 0.12 70 / 0.30)",
          }}
        >
          {warnings.map((warning, index) => (
            <li key={`${view.key}-warning-${index}`}>{warning}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
