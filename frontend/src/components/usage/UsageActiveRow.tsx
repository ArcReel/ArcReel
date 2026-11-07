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
  /** Only rows represented by tasks may be cancellable; taskless pending calls use null. */
  task: TaskItem | null;
  /** Provider ID → display name, falling back to the ID when unknown. */
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
 * One in-progress row. The left column combines project tasks from the task store and pending calls without task representatives,
 * such as script generation and assistant sessions. Only queued tasks permit cancellation:
 * running tasks finish normally, and pending calls have no task to cancel.
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
