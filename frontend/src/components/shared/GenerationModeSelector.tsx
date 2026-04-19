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

const MODES: GenerationMode[] = ["storyboard", "grid", "reference_video"];

export function GenerationModeSelector({
  value,
  onChange,
  disabledModes = [],
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
              ? "cursor-pointer rounded-md border px-3 py-1 text-xs transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-indigo-500"
              : "flex-1 cursor-pointer rounded-lg border px-3 py-2 text-center text-sm transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-indigo-500";
          const stateClass = disabled
            ? "border-gray-800 bg-gray-900 text-gray-600 cursor-not-allowed"
            : selected
              ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
              : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600";
          return (
            <label key={m} className={`${baseClass} ${stateClass}`}>
              <input
                type="radio"
                name={name}
                value={m}
                checked={selected}
                disabled={disabled}
                onChange={() => onChange(m)}
                className="sr-only"
              />
              {labelFor(m)}
            </label>
          );
        })}
      </div>
      {size === "lg" && (
        <p className="text-xs text-gray-500">{descFor(value)}</p>
      )}
    </div>
  );
}
