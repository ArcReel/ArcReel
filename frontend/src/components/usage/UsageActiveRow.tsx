import { Loader2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import type { TaskItem } from "@/types";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import { useNowTick } from "@/hooks/useNowTick";
import { TargetLabel } from "./RecordRow";
import { MEDIA_META, elapsedSince } from "./usage-record-format";
import type { UsageRecordView } from "./usage-record-view";

interface UsageActiveRowProps {
  view: UsageRecordView;
  /** 有任务代表的行才可能可取消；无任务的 pending 调用为 null。 */
  task: TaskItem | null;
  /** 供应商 id → 显示名，查不到回退 id。 */
  providerLabel: (provider: string | null) => string;
  onCancel?: (taskId: string) => void;
  cancelling?: boolean;
}

const TASK_STATUS_KEYS: Record<TaskItem["status"], string> = {
  running: "generating_status",
  queued: "queued_status",
  succeeded: "completed_status",
  failed: "failed_status",
  cancelled: "cancelled_status",
};

/**
 * 进行中区的一行。左栏同时容纳两种来源：任务 store 里项目内进行中的任务，以及没有
 * 任务代表的 pending 调用（剧本生成、助手会话一类）。只有排队中的任务可取消：
 * 已开始执行的任务照常跑完，pending 调用没有任务可取消。
 */
export function UsageActiveRow({
  view,
  task,
  providerLabel,
  onCancel,
  cancelling,
}: UsageActiveRowProps) {
  const { t } = useTranslation("dashboard");
  const now = useNowTick();

  const media = MEDIA_META[view.mediaType];
  const MediaIcon = media.Icon;
  const running = task?.status === "running";
  const cancellable = task?.status === "queued";
  const statusText = task
    ? (task.error_message ?? t(TASK_STATUS_KEYS[task.status]))
    : t("usage_status_pending");

  return (
    <div className="flex items-start gap-2 px-2 py-1.5">
      <MediaIcon role="img" aria-label={t(media.labelKey)} className={cn("mt-0.5 size-3.5 shrink-0", media.iconClass)} />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex items-center gap-1.5 text-sm">
          <TargetLabel record={view} />
          <span
            aria-hidden="true"
            className={cn("ml-auto size-1.5 shrink-0 rounded-full bg-primary", running && "animate-breath")}
          />
          <span className="num shrink-0 text-xs text-muted-foreground">{elapsedSince(view.startedAt, now, t)}</span>
          {task && cancellable && onCancel && (
            <Button
              variant="ghost"
              size="icon-xs"
              disabled={cancelling}
              onClick={() => onCancel(task.task_id)}
              aria-label={t(cancelling ? "cancelling_status" : "cancel_this_task")}
            >
              {cancelling ? <Loader2 aria-hidden="true" className="animate-spin" /> : <X aria-hidden="true" />}
            </Button>
          )}
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <TruncatedText text={`${providerLabel(view.provider)} · ${view.model ?? t("usage_model_unresolved")}`} />
          <span className="ml-auto max-w-1/2 shrink-0 truncate">{statusText}</span>
        </div>
      </div>
    </div>
  );
}
