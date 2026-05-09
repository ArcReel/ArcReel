import { useId, useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Loader2 } from "lucide-react";
import { useEscapeClose } from "@/hooks/useEscapeClose";
import { useFocusTrap } from "@/hooks/useFocusTrap";

export type ConfirmTone = "default" | "danger";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: ReactNode;
  confirmLabel: string;
  loadingLabel?: string;
  cancelLabel?: string;
  tone?: ConfirmTone;
  loading?: boolean;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

const CANCEL_BTN_CLS =
  "rounded-lg border border-hairline px-4 py-2 text-sm text-text-2 transition-colors hover:border-hairline-strong hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-60";

const ACCENT_CONFIRM_CLS =
  "inline-flex items-center gap-1.5 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-[oklch(0.14_0_0)] shadow-[inset_0_1px_0_oklch(1_0_0_/_0.3),0_4px_14px_-6px_var(--color-accent)] transition-colors hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-60";

const WARM_CONFIRM_CLS =
  "inline-flex items-center gap-1.5 rounded-lg border border-warm-ring bg-warm-tint px-4 py-2 text-sm font-medium text-warm-bright transition-colors hover:border-warm-bright/60 hover:bg-warm-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-warm-ring disabled:cursor-not-allowed disabled:opacity-60";

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  loadingLabel,
  cancelLabel,
  tone = "default",
  loading = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const { t } = useTranslation("common");
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descId = useId();
  useEscapeClose(onCancel, open && !loading);
  useFocusTrap(dialogRef, open);

  if (!open) return null;

  const isDanger = tone === "danger";
  const confirmCls = isDanger ? WARM_CONFIRM_CLS : ACCENT_CONFIRM_CLS;
  const resolvedCancelLabel = cancelLabel ?? t("cancel");
  const resolvedLoadingLabel = loadingLabel ?? confirmLabel;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descId : undefined}
        className="w-full max-w-md overflow-hidden rounded-2xl border border-hairline bg-bg-grad-a p-6 shadow-2xl"
      >
        <div className="flex items-start gap-4">
          {isDanger && (
            <div
              aria-hidden
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-warm-tint text-warm-bright"
            >
              <AlertTriangle className="h-6 w-6" />
            </div>
          )}
          <div className="space-y-2">
            <h2 id={titleId} className="text-lg font-semibold text-text">
              {title}
            </h2>
            {description && (
              <div id={descId} className="text-sm leading-6 text-text-3">
                {description}
              </div>
            )}
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={loading}
            className={CANCEL_BTN_CLS}
          >
            {resolvedCancelLabel}
          </button>
          <button
            type="button"
            onClick={() => void onConfirm()}
            disabled={loading}
            className={confirmCls}
          >
            {loading && <Loader2 className="h-4 w-4 motion-safe:animate-spin" />}
            {loading ? resolvedLoadingLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
