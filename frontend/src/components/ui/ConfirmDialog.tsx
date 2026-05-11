import { useId, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Loader2 } from "lucide-react";
import { GlassModal } from "./GlassModal";
import { PrimaryButton } from "./PrimaryButton";
import { SecondaryButton } from "./SecondaryButton";
import { WARM_TONE } from "@/utils/severity-tone";

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

// 通用确认弹窗（站内 yes/no 类破坏性确认的入口）。
// tone="danger" 时顶部 hairline 走 warm、确认按钮走 warm tone；并显示左上角告警 icon。
// 视觉与其他 v3 玻璃 modal 统一（issue #487）。
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
  const titleId = useId();
  const descId = useId();

  const isDanger = tone === "danger";
  const resolvedCancelLabel = cancelLabel ?? t("cancel");
  const resolvedLoadingLabel = loadingLabel ?? confirmLabel;

  return (
    <GlassModal
      open={open}
      onClose={loading ? () => {} : onCancel}
      labelledBy={titleId}
      describedBy={description ? descId : undefined}
      hairlineTone={isDanger ? "warm" : "accent"}
      closeOnBackdrop={!loading}
      closeOnEscape={!loading}
    >
      <div className="px-6 pb-6 pt-5">
        <div className="flex items-start gap-3">
          {isDanger && (
            <span
              aria-hidden
              className="grid h-9 w-9 shrink-0 place-items-center rounded-xl"
              style={{
                background:
                  "linear-gradient(135deg, var(--color-warm-tint), var(--color-warm-tint-faint))",
                border: `1px solid ${WARM_TONE.ring}`,
                color: WARM_TONE.color,
                boxShadow: `0 8px 18px -8px ${WARM_TONE.glow}`,
              }}
            >
              <AlertTriangle className="h-4 w-4" />
            </span>
          )}
          <div className="min-w-0 flex-1">
            <h2
              id={titleId}
              className="display-serif text-[17px] font-semibold tracking-tight"
              style={{ color: "var(--color-text)" }}
            >
              {title}
            </h2>
            {description && (
              <div
                id={descId}
                className="mt-1 text-[12.5px] leading-relaxed"
                style={{ color: "var(--color-text-3)" }}
              >
                {description}
              </div>
            )}
          </div>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <SecondaryButton
            size="sm"
            onClick={onCancel}
            disabled={loading}
          >
            {resolvedCancelLabel}
          </SecondaryButton>
          <PrimaryButton
            size="sm"
            tone={isDanger ? "warm" : "accent"}
            onClick={() => void onConfirm()}
            disabled={loading}
            leadingIcon={
              loading ? (
                <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" />
              ) : undefined
            }
          >
            {loading ? resolvedLoadingLabel : confirmLabel}
          </PrimaryButton>
        </div>
      </div>
    </GlassModal>
  );
}
