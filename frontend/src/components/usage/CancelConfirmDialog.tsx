import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { voidPromise } from "@/utils/async";
import { episodeItemRefLabel } from "@/utils/episode-display";
import { dismissOnEscape, dismissOnFocusLeave, keepFocusInside } from "@/utils/inline-confirm";
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
 *
 * 焦点按行内确认的约定（docs/standards/frontend.md）：打开时落在「返回」上；请求在途时两个按钮
 * 禁用，请求失败、按钮解除禁用后焦点回到「返回」；「返回」或 Esc 退出确认并把焦点交还触发它的元素；焦点离开确认区域
 * 也退出，焦点留在用户去的地方。确认成功后由调用方卸载。
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
  const backRef = useRef<HTMLButtonElement>(null);
  const triggerRef = useRef<HTMLElement | null>(null);

  // 预览请求返回前焦点仍在触发取消的按钮上，挂载时记下它，退出确认时交还
  useEffect(() => {
    const active = document.activeElement;
    if (active instanceof HTMLElement && active !== document.body) triggerRef.current = active;
  }, []);

  useEffect(() => {
    if (!cancelling) backRef.current?.focus();
  }, [cancelling]);

  const dismissedRef = useRef(false);

  const dismiss = (returnFocus: boolean) => {
    // 交还焦点会触发区域的 blur，已退出时不再重复退出
    if (cancelling || dismissedRef.current) return;
    dismissedRef.current = true;
    const trigger = triggerRef.current;
    onDismiss();
    if (returnFocus && trigger?.isConnected) trigger.focus();
  };
  return (
    // react-doctor-disable-next-line react-doctor/prefer-html-dialog -- 行内非模态确认，留在所在列表里不进顶层；原生 <dialog> 的价值在 showModal 的模态行为，这里不需要
    <div
      role="alertdialog"
      aria-label={t("cancel_confirm_aria")}
      className="flex flex-col gap-2 border-t border-border bg-muted/40 px-4 py-3"
      onBlur={(event) => dismissOnFocusLeave(event, dismiss)}
      onPointerDown={keepFocusInside}
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
        <Button
          size="sm"
          variant="destructive"
          onClick={voidPromise(onConfirm)}
          onKeyDown={(event) => dismissOnEscape(event, dismiss)}
          disabled={cancelling}
        >
          {cancelling ? t("cancelling") : t("confirm_cancel")}
        </Button>
        <Button
          ref={backRef}
          size="sm"
          variant="outline"
          onClick={() => dismiss(true)}
          onKeyDown={(event) => dismissOnEscape(event, dismiss)}
          disabled={cancelling}
        >
          {t("go_back")}
        </Button>
      </div>
    </div>
  );
}
