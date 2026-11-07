import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Input } from "@/components/ui/input";

/** Common target-duration tiers (seconds); other positive integer seconds use Custom. */
const DURATION_TIERS = [15, 30, 60, 90] as const;

function isTier(value: number | null): boolean {
  return DURATION_TIERS.some((tier) => tier === value);
}

function parseSeconds(text: string): number | null {
  if (!/^\d+$/.test(text.trim())) return null;
  const value = Number(text.trim());
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

function chipClass(active: boolean, disabled: boolean): string {
  return cn(
    "relative inline-flex h-8 cursor-pointer items-center rounded-lg border px-3 text-sm tabular-nums transition-colors has-focus-visible:ring-3 has-focus-visible:ring-ring/50",
    active ? "border-primary/50 bg-primary/10 text-foreground" : "border-border text-subtle-foreground hover:text-foreground",
    disabled && "cursor-not-allowed opacity-50",
  );
}

export interface DurationTierPickerProps {
  /** Current seconds; null for Custom inputs that are not positive integers so callers can block submission. */
  value: number | null;
  onChange: (value: number | null) => void;
  /** Translated field names, also radio-group accessible names; defaults to Total target duration. */
  label?: string;
  disabled?: boolean;
}

/**
 * Duration-tier picker: 15 / 30 / 60 / 90-second radio options; other positive integer seconds enter through Custom.
 * Custom inputs that are not positive integers report null and explain inline.
 * External changes to non-tier values (e.g. discarding edits) automatically select Custom and fill inputs.
 */
export function DurationTierPicker({ value, onChange, label, disabled = false }: DurationTierPickerProps) {
  const { t } = useTranslation("dashboard");
  const reactId = useId();
  const labelId = `${reactId}-label`;
  const errorId = `${reactId}-error`;
  const fieldLabel = label ?? t("target_duration_label");

  const [custom, setCustom] = useState(value !== null && !isTier(value));
  const [customText, setCustomText] = useState(value !== null && !isTier(value) ? String(value) : "");
  const [prevValue, setPrevValue] = useState(value);
  // Synchronize internal state to external writes without interrupting ongoing Custom input echoed back from callers.
  if (value !== prevValue) {
    setPrevValue(value);
    if (value !== null && value !== parseSeconds(customText)) {
      setCustom(!isTier(value));
      setCustomText(isTier(value) ? "" : String(value));
    }
  }

  const invalid = custom && value === null;

  return (
    <div className="flex flex-col gap-1.5">
      <div id={labelId} className="text-xs font-medium text-muted-foreground">
        {fieldLabel}
      </div>
      <div role="radiogroup" aria-labelledby={labelId} className="flex flex-wrap items-center gap-2">
        {DURATION_TIERS.map((tier) => (
          <label key={tier} className={chipClass(!custom && value === tier, disabled)}>
            <input
              type="radio"
              name={reactId}
              checked={!custom && value === tier}
              onChange={() => {
                setCustom(false);
                onChange(tier);
              }}
              disabled={disabled}
              className="sr-only"
            />
            {t("duration_seconds_value_text", { value: tier })}
          </label>
        ))}
        <label className={chipClass(custom, disabled)}>
          <input
            type="radio"
            name={reactId}
            checked={custom}
            onChange={() => {
              setCustom(true);
              onChange(parseSeconds(customText));
            }}
            disabled={disabled}
            className="sr-only"
          />
          {t("ad_target_duration_custom")}
        </label>
        {custom && (
          <div className="flex items-center gap-2">
            <Input
              type="number"
              inputMode="numeric"
              min={1}
              step={1}
              value={customText}
              onChange={(event) => {
                setCustomText(event.target.value);
                onChange(parseSeconds(event.target.value));
              }}
              disabled={disabled}
              aria-label={t("ad_target_duration_custom_label", { label: fieldLabel })}
              aria-invalid={invalid || undefined}
              aria-describedby={invalid ? errorId : undefined}
              className="w-24"
            />
            <span className="text-xs text-muted-foreground">{t("episode_target_duration_unit")}</span>
          </div>
        )}
      </div>
      {invalid && (
        <p id={errorId} role="alert" className="text-xs text-destructive">
          {t("ad_target_duration_invalid")}
        </p>
      )}
    </div>
  );
}
