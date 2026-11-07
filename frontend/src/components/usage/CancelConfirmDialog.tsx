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
  /** Localized server cancellation-rejection reasons; empty means generic failure copy. */
  failureDetail?: string | null;
  onConfirm: () => Promise<void>;
  onDismiss: () => void;
}

/**
 * Cancellation confirmation: inline `alertdialog`, belonging to list actions; screen-centered dialogs would lose
 * context about which rows users cancel.
 *
 * Follow inline-confirmation focus rules (docs/standards/frontend.md): initially focus Back; disable both buttons during
 * requests, returning focus to Back when failed requests reenable them. Back/Esc exits and returns focus to triggers; leaving confirmation regions
 * also exits while retaining focus at user destinations. Callers unmount after successful confirmation.
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

  // Focus remains on cancellation triggers before previews return; capture at mount and restore when exiting confirmation.
  useEffect(() => {
    const active = document.activeElement;
    if (active instanceof HTMLElement && active !== document.body) triggerRef.current = active;
  }, []);

  useEffect(() => {
    if (!cancelling) backRef.current?.focus();
  }, [cancelling]);

  const dismissedRef = useRef(false);

  const dismiss = (returnFocus: boolean) => {
    // Focus restoration triggers region blur; never exit twice after already exiting.
    if (cancelling || dismissedRef.current) return;
    dismissedRef.current = true;
    const trigger = triggerRef.current;
    onDismiss();
    if (returnFocus && trigger?.isConnected) trigger.focus();
  };
  return (
    // react-doctor-disable-next-line react-doctor/prefer-html-dialog -- Inline nonmodal confirmations remain in lists, not top layers; native <dialog> value lies in showModal modality, unnecessary here
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
