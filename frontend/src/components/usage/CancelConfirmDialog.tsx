import { useTranslation } from "react-i18next";

import { voidPromise } from "@/utils/async";
import type { CancelRequest } from "./use-task-cancellation";

interface CancelConfirmDialogProps {
  request: CancelRequest;
  cancelling: boolean;
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
  onConfirm,
  onDismiss,
}: CancelConfirmDialogProps) {
  const { t } = useTranslation("dashboard");
  const cascaded = request.kind === "single" ? request.cascaded : [];

  return (
    <div
      role="alertdialog"
      aria-label={t("cancel_confirm_aria")}
      className="border-t border-hairline-soft px-4 py-3"
      style={{ background: "oklch(0.16 0.010 265 / 0.5)" }}
    >
      <p className="text-[12px] text-text-2">
        {request.kind === "all"
          ? t("cancel_all_confirm", { count: request.queuedCount })
          : cascaded.length > 0
            ? t("cancel_cascade_msg", { count: cascaded.length })
            : t("cancel_single_confirm")}
      </p>
      {cascaded.length > 0 && (
        <ul className="num mt-1.5 max-h-20 overflow-y-auto text-[10.5px] text-text-4">
          {cascaded.map((task) => (
            <li key={task.task_id}>
              {t(`task_type_${task.task_type}`, { defaultValue: task.task_type })} /{" "}
              {task.resource_id}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-2.5 flex gap-2">
        <button
          type="button"
          onClick={voidPromise(onConfirm)}
          disabled={cancelling}
          className="focus-ring rounded px-2.5 py-1 text-[11px] font-medium transition-transform disabled:opacity-50"
          style={{
            color: "oklch(0.98 0 0)",
            background: "linear-gradient(135deg, oklch(0.55 0.20 25), oklch(0.45 0.18 25))",
            boxShadow:
              "inset 0 1px 0 oklch(1 0 0 / 0.18), 0 4px 14px -4px oklch(0.40 0.18 25 / 0.5)",
          }}
        >
          {cancelling ? t("cancelling") : t("confirm_cancel")}
        </button>
        <button
          type="button"
          onClick={onDismiss}
          className="focus-ring rounded border border-hairline bg-bg-grad-a/50 px-2.5 py-1 text-[11px] text-text-3 transition-colors hover:text-text"
        >
          {t("go_back")}
        </button>
      </div>
    </div>
  );
}
