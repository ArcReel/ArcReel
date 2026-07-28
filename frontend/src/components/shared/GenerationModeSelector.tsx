import { useTranslation } from "react-i18next";
import type { GenerationMode } from "@/utils/generation-mode";

export interface GenerationModeSelectorProps {
  value: GenerationMode;
  onChange: (next: GenerationMode) => void;
  /** Modes to disable (e.g. if a provider cannot support reference_video). */
  disabledModes?: GenerationMode[];
  /** "lg" for wizard/settings (with description), "sm" for toolbars. */
  size?: "lg" | "sm";
  /** Optional name to differentiate multiple selectors on the same page. */
  name?: string;
}

const EMPTY_DISABLED: readonly GenerationMode[] = Object.freeze([]);

const MODES = ["storyboard", "reference_video", "grid"] as const satisfies readonly GenerationMode[];

export function GenerationModeSelector({
  value,
  onChange,
  disabledModes = EMPTY_DISABLED as GenerationMode[],
  size = "lg",
  name = "generationMode",
}: GenerationModeSelectorProps) {
  const { t } = useTranslation("dashboard");

  const labelFor = (m: GenerationMode): string =>
    m === "storyboard"
      ? t("mode_storyboard")
      : m === "grid"
        ? t("mode_grid")
        : t("mode_reference_video");

  const descFor = (m: GenerationMode): string =>
    m === "storyboard"
      ? t("mode_storyboard_desc")
      : m === "grid"
        ? t("mode_grid_desc")
        : t("mode_reference_video_desc");

  return (
    <div className="space-y-2">
      <div
        role="radiogroup"
        aria-label={t("generation_mode")}
        className={size === "sm" ? "inline-flex gap-1" : "flex gap-3"}
      >
        {MODES.map((m) => {
          const disabled = disabledModes.includes(m);
          const selected = value === m;
          const baseClass =
            size === "sm"
              ? "cursor-pointer rounded-[6px] border px-3 py-1 text-[11.5px] transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent"
              : "flex-1 cursor-pointer rounded-[8px] border px-3 py-2.5 text-center text-[13px] transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent";
          const stateClass = disabled
            ? "border-hairline-soft bg-bg-grad-a/35 text-text-4 cursor-not-allowed"
            : selected
              ? "border-accent/55 bg-accent-dim text-accent-2"
              : "border-hairline bg-bg-grad-a/55 text-text-2 hover:border-hairline-strong hover:text-text";
          return (
            <label
              key={m}
              className={`${baseClass} ${stateClass}`}
              style={
                selected && !disabled
                  ? { boxShadow: "0 0 18px -8px var(--color-accent-glow)" }
                  : undefined
              }
            >
              <input
                type="radio"
                name={name}
                value={m}
                checked={selected}
                disabled={disabled}
                onChange={() => { if (!disabled) onChange(m); }}
                className="sr-only"
              />
              {labelFor(m)}
            </label>
          );
        })}
      </div>
      {size === "lg" && (
        <p className="text-[12px] leading-[1.55] text-text-3">{descFor(value)}</p>
      )}
    </div>
  );
}
