import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { DurationOutOfRangeReason } from "@/hooks/useModelCapabilities";

const INCOMPATIBLE_KEY: Record<DurationOutOfRangeReason, string> = {
  model: "duration_incompatible_warning",
  resolution: "duration_incompatible_resolution_warning",
  reference: "duration_incompatible_reference_warning",
};

/**
 * 时长不在档位内的说明。文案按成因分开：模型全集就不含该值才是「模型不支持」，被分辨率 / 参考图
 * 路径的联动约束收窄掉时说清是哪一条，用户据此改对应设置。
 */
export function durationIncompatibleLabel(
  t: TFunction,
  seconds: number,
  options: readonly number[],
  reason: DurationOutOfRangeReason | null | undefined,
): string {
  return t(INCOMPATIBLE_KEY[reason ?? "model"], { value: seconds, supported: options.join(", ") });
}

interface PlanDurationSelectProps {
  seconds: number;
  /** 可选档位（升序）；为 null 或空时只显示秒数。 */
  options: number[] | null;
  onChange: (seconds: number) => void;
  label: string;
  disabled?: boolean;
  /** 时长由端点固定：附上说明；有档位（剧本规划借用的档位）时仍可选。 */
  endpointFixed?: boolean;
}

/** 脚本规划条目的时长下拉，内容确认页三种规划共用。 */
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
  // 存量秒数可能已不在当前档位内：补一个当前值选项，否则下拉显示不出盘上的秒数。
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
