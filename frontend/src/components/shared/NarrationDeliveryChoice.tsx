import { useTranslation } from "react-i18next";
import type { ReferenceRequestOptions } from "@/types";

type NarrationDelivery = NonNullable<ReferenceRequestOptions["narration_delivery"]>;

interface Props {
  value: NarrationDelivery;
  onChange: (value: NarrationDelivery) => void;
  disabled?: boolean;
  compact?: boolean;
}

/** Request-local narration delivery choice; callers must not persist it into script/project state. */
export function NarrationDeliveryChoice({ value, onChange, disabled, compact = false }: Props) {
  const { t } = useTranslation("dashboard");
  return (
    <div
      role="group"
      aria-label={t("narration_delivery_label")}
      className={`inline-flex items-center gap-2 ${compact ? "text-[11.5px]" : "text-xs"}`}
    >
      <span className="text-[var(--color-text-3)]">{t("narration_delivery_label")}</span>
      <span className="inline-flex overflow-hidden rounded-md border border-[var(--color-hairline)]">
        {(["post_production", "use_tts"] as const).map((choice) => (
          <button
            key={choice}
            type="button"
            aria-pressed={value === choice}
            disabled={disabled}
            onClick={() => onChange(choice)}
            className={`focus-ring px-2 py-1 transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
              value === choice
                ? "bg-[var(--color-accent-dim)] text-[var(--color-accent-2)]"
                : "bg-[oklch(0.22_0.011_265_/_0.7)] text-[var(--color-text-3)]"
            }`}
          >
            {t(
              choice === "use_tts"
                ? "narration_delivery_use_tts"
                : "narration_delivery_post_production",
            )}
          </button>
        ))}
      </span>
    </div>
  );
}
