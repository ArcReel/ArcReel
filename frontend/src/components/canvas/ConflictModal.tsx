import { useRef } from "react";
import { AlertTriangle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useEscapeClose } from "@/hooks/useEscapeClose";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { WARM_TONE } from "@/utils/severity-tone";

export type ConflictResolution = "replace" | "rename" | "cancel";

interface ConflictModalProps {
  existing: string;
  suggestedName: string;
  onResolve: (decision: ConflictResolution) => void;
}

const PANEL_BG =
  "linear-gradient(180deg, oklch(0.21 0.012 265 / 0.96), oklch(0.18 0.010 265 / 0.96))";

export function ConflictModal({ existing, suggestedName, onResolve }: ConflictModalProps) {
  const { t } = useTranslation("common");
  const cancel = () => onResolve("cancel");
  const dialogRef = useRef<HTMLDivElement>(null);
  useEscapeClose(cancel);
  useFocusTrap(dialogRef);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        aria-label={t("close")}
        onClick={cancel}
        className="absolute inset-0"
        style={{
          background: "oklch(0 0 0 / 0.65)",
          backdropFilter: "blur(2px)",
          WebkitBackdropFilter: "blur(2px)",
        }}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="conflict-modal-title"
        className="relative w-full max-w-md overflow-hidden rounded-xl"
        style={{
          background: PANEL_BG,
          border: "1px solid var(--color-hairline)",
          backdropFilter: "blur(14px)",
          WebkitBackdropFilter: "blur(14px)",
          boxShadow:
            "0 24px 60px -12px oklch(0 0 0 / 0.6), inset 0 1px 0 oklch(1 0 0 / 0.05)",
        }}
      >
        <span
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-px"
          style={{
            background:
              "linear-gradient(90deg, transparent, var(--color-warm-fade), transparent)",
          }}
        />

        <div className="px-6 pb-2 pt-5">
          <div className="flex items-start gap-3">
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
            <div className="min-w-0 flex-1">
              <h2
                id="conflict-modal-title"
                className="display-serif text-[17px] font-semibold tracking-tight"
                style={{ color: "var(--color-text)" }}
              >
                {t("conflict_modal_title")}
              </h2>
              <p
                className="mt-1 text-[12.5px] leading-relaxed"
                style={{ color: "var(--color-text-3)" }}
              >
                {t("conflict_modal_desc", { filename: existing })}
              </p>
            </div>
          </div>
        </div>

        <div className="px-6 pb-5">
          <div
            className="num mt-3 truncate rounded-md px-3 py-2 text-[12px]"
            style={{
              background: "oklch(0.16 0.010 265 / 0.6)",
              border: "1px solid var(--color-hairline-soft)",
              color: "var(--color-text-2)",
            }}
            title={suggestedName}
          >
            <span style={{ color: "var(--color-text-4)" }}>{"→ "}</span>
            {suggestedName}
          </div>

          <div className="mt-5 flex justify-end gap-2">
            <SecondaryButton onClick={cancel}>{t("cancel")}</SecondaryButton>
            <SecondaryButton onClick={() => onResolve("rename")}>
              {t("keep_both")}
            </SecondaryButton>
            <DangerButton onClick={() => onResolve("replace")}>
              {t("replace")}
            </DangerButton>
          </div>
        </div>
      </div>
    </div>
  );
}

function SecondaryButton({
  onClick,
  children,
}: {
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="focus-ring rounded-md px-3 py-1.5 text-[12px] transition-colors"
      style={{
        color: "var(--color-text-3)",
        border: "1px solid var(--color-hairline)",
        background: "oklch(0.22 0.011 265 / 0.5)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.color = "var(--color-text)";
        e.currentTarget.style.background = "oklch(0.26 0.013 265 / 0.7)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.color = "var(--color-text-3)";
        e.currentTarget.style.background = "oklch(0.22 0.011 265 / 0.5)";
      }}
    >
      {children}
    </button>
  );
}

// Replace 是有损动作（覆盖现有文件），所以用红色 danger 渐变而不是默认紫色 CTA。
function DangerButton({
  onClick,
  children,
}: {
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="focus-ring rounded-md px-4 py-1.5 text-[12px] font-medium transition-transform"
      style={{
        color: "oklch(0.14 0 0)",
        background:
          "linear-gradient(135deg, var(--color-danger-bright), var(--color-danger-2))",
        boxShadow:
          "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -6px var(--color-danger-glow), 0 0 0 1px var(--color-danger-ring)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.transform = "translateY(-1px)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = "translateY(0)";
      }}
    >
      {children}
    </button>
  );
}
