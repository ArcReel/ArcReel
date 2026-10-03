import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { voidPromise } from "@/utils/async";
import { episodeItemRefLabel } from "@/utils/episode-display";
import type { CancelRequest } from "./use-task-cancellation";

interface CancelConfirmDialogProps {
  request: CancelRequest;
  cancelling: boolean;
  failed: boolean;
  /** 服务端拒绝取消的已本地化原因；为空时显示通用失败文案。 */
  failureDetail?: string | null;
  onConfirm: () => Promise<void>;
  onDismiss: () => void;
}

/**
 * 取消确认。行内 `alertdialog`：它属于所在列表的一次操作，弹到屏幕中央会让用户
 * 失去「取消的是哪一行」的上下文。
 */
export function CancelConfirmDialog({
  request,
  cancelling,
  failed,
  failureDetail,
  onConfirm,
  onDismiss,
}: CancelConfirmDialogProps) {
  const { t } = useTranslation("dashboard");
  const cascaded = request.kind === "single" ? request.cascaded : [];

  return (
    <div
      role="alertdialog"
      aria-label={t("cancel_confirm_aria")}
      className="flex flex-col gap-2 border-t border-border bg-muted/40 px-4 py-3"
    >
      <p className="text-sm text-subtle-foreground">
        {request.kind === "all"
          ? t("cancel_all_confirm", { count: request.queuedCount })
          : cascaded.length > 0
            ? t("cancel_cascade_msg", { count: cascaded.length })
            : t("cancel_single_confirm")}
      </p>
      {cascaded.length > 0 && (
        <ul className="num relative max-h-20 overflow-y-auto text-xs text-muted-foreground">
          {cascaded.map((task) => (
            <li key={task.task_id}>
              {t(`task_type_${task.task_type}`, { defaultValue: task.task_type })} /{" "}
              {episodeItemRefLabel(task.resource_id, task.resource_ref, t)}
            </li>
          ))}
        </ul>
      )}
      {failed && (
        <p role="alert" className="text-xs text-destructive">
          {failureDetail ?? t("cancel_failed")}
        </p>
      )}
      <div className="flex gap-2">
        <Button size="sm" variant="destructive" onClick={voidPromise(onConfirm)} disabled={cancelling}>
          {cancelling ? t("cancelling") : t("confirm_cancel")}
        </Button>
        <Button size="sm" variant="outline" onClick={onDismiss}>
          {t("go_back")}
        </Button>
      </div>
    </div>
  );
}
