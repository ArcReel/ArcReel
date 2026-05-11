import { useId } from "react";
import { AlertTriangle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { GlassModal } from "@/components/ui/GlassModal";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { WARM_TONE } from "@/utils/severity-tone";

export type ConflictResolution = "replace" | "rename" | "cancel";

interface ConflictModalProps {
  existing: string;
  suggestedName: string;
  onResolve: (decision: ConflictResolution) => void;
}

export function ConflictModal({ existing, suggestedName, onResolve }: ConflictModalProps) {
  const { t } = useTranslation("common");
  const cancel = () => onResolve("cancel");
  const titleId = useId();

  return (
    <GlassModal
      open
      onClose={cancel}
      labelledBy={titleId}
      hairlineTone="warm"
    >
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
              id={titleId}
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
          <SecondaryButton size="sm" onClick={cancel}>
            {t("cancel")}
          </SecondaryButton>
          <SecondaryButton size="sm" onClick={() => onResolve("rename")}>
            {t("keep_both")}
          </SecondaryButton>
          <PrimaryButton size="sm" tone="danger" onClick={() => onResolve("replace")}>
            {t("replace")}
          </PrimaryButton>
        </div>
      </div>
    </GlassModal>
  );
}
