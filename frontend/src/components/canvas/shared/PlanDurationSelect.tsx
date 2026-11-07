import { useTranslation } from "react-i18next";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

interface PlanDurationSelectProps {
  seconds: number;
  /** Available tiers in ascending order; null or empty shows seconds only. */
  options: number[] | null;
  onChange: (seconds: number) => void;
  label: string;
  disabled?: boolean;
  /** Endpoint-fixed duration: attach an explanation; remain selectable if tiers exist, as reused by script planning. */
  endpointFixed?: boolean;
}

/** Planning-entry duration dropdown shared by all three plan types on content confirmation pages. */
export function PlanDurationSelect({
  seconds,
  options,
  onChange,
  label,
  disabled = false,
  endpointFixed = false,
}: PlanDurationSelectProps) {
  const { t } = useTranslation("dashboard");
  const notice = endpointFixed ? t("duration_not_driven_notice") : undefined;
  if (!options?.length) {
    return (
      <span className="text-xs text-muted-foreground">
        {t("reference_script_plan_duration_option", { seconds })}
        {notice && ` · ${notice}`}
      </span>
    );
  }
  // Existing seconds may no longer be a current tier: add the current value as an option so the dropdown can show the persisted duration.
  const values = options.includes(seconds) ? options : [...options, seconds].sort((a, b) => a - b);
  const items = values.map((value) => ({ value, label: t("reference_script_plan_duration_option", { seconds: value }) }));
  const select = (
    <Select
      items={items}
      value={seconds}
      onValueChange={(next) => {
        if (next !== null) onChange(next);
      }}
      disabled={disabled}
    >
      <SelectTrigger size="sm" aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent alignItemWithTrigger={false} align="start" className="w-auto">
        {items.map((item) => (
          <SelectItem key={item.value} value={item.value}>
            {item.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
  if (!notice) return select;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
      {select}
      {notice}
    </span>
  );
}
