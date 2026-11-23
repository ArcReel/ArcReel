import { useTranslation } from "react-i18next";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

interface PlanDurationSelectProps {
  seconds: number;
  options: number[] | null;
  onChange: (seconds: number) => void;
  label: string;
  disabled?: boolean;
  endpointFixed?: boolean;
}

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
