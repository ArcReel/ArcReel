import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
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
      <span className="text-[11px] text-text-4" title={notice}>
        {t("reference_script_plan_duration_option", { seconds })}
        {notice && ` · ${notice}`}
      </span>
    );
  }
  const select = (
    <select
      value={seconds}
      onChange={(e) => onChange(Number(e.target.value))}
      disabled={disabled}
      aria-label={label}
      className="rounded-[6px] border border-hairline bg-bg-grad-a/40 px-1 py-0.5 text-[11px] text-text-3 hover:text-text disabled:cursor-not-allowed disabled:opacity-60"
    >
      {/* 存量秒数可能已不在当前档位内：补一个当前值选项，否则 select 会静默跳到首档，
          用户看到的秒数与盘上的对不上。 */}
      {(options.includes(seconds) ? options : [...options, seconds].sort((a, b) => a - b)).map((d) => (
        <option key={d} value={d}>
          {t("reference_script_plan_duration_option", { seconds: d })}
        </option>
      ))}
    </select>
  );
  if (!notice) return select;
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-text-4" title={notice}>
      {select}
      {notice}
    </span>
  );
}
