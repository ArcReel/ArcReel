import { useTranslation } from "react-i18next";
import { ProviderModelSelect } from "@/components/ui/ProviderModelSelect";

/** 文本任务档位取值（docs/adr/0049）。字段名即档位键：调用点在代码里固定归档，用户只配置每档 backend。 */
export interface TextTierValue {
  default: string;
  simple: string;
  complex: string;
}

type Tier = keyof TextTierValue;

const TIERS: readonly { key: Tier; labelKey: string; captionKey: string }[] = [
  { key: "default", labelKey: "text_tier_default_label", captionKey: "text_tier_default_caption" },
  { key: "simple", labelKey: "text_tier_simple_label", captionKey: "text_tier_simple_caption" },
  { key: "complex", labelKey: "text_tier_complex_label", captionKey: "text_tier_complex_caption" },
] as const;

export interface TextTierFieldsProps {
  value: TextTierValue;
  onChange: (next: TextTierValue) => void;
  options: string[];
  providerNames: Record<string, string>;
  /** 空档位选项的标签：全局设置为「自动选择」，项目/向导为「使用全局默认」。 */
  defaultLabel: string;
  /** 各档空选项旁的次要提示（如全局设置的「自动」）。 */
  hints?: Partial<TextTierValue>;
  /**
   * 各档留空时的实际生效值（按项目优先解析链算好后传入，格式 provider/model）。
   * 触发按钮以「跟随全局默认 · 生效值」呈现，让用户看到继承结果。全局设置不传（它即基准）。
   */
  fallbacks?: Partial<TextTierValue>;
}

/**
 * 文本档位配置的同源组件：默认模型 / 简单任务 / 复杂任务三档下拉，每档下方常驻说明列出覆盖调用点，
 * 卡片底部注明 Agent 供应商边界。全局设置、项目设置、创建向导三处复用，保证覆盖范围文案单一真相源。
 */
export function TextTierFields({
  value,
  onChange,
  options,
  providerNames,
  defaultLabel,
  hints,
  fallbacks,
}: TextTierFieldsProps) {
  const { t } = useTranslation("templates");
  return (
    <div className="space-y-3.5">
      {TIERS.map(({ key, labelKey, captionKey }) => (
        <div key={key}>
          <div className="mb-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4">
            {t(labelKey)}
          </div>
          <ProviderModelSelect
            value={value[key]}
            options={options}
            providerNames={providerNames}
            onChange={(next) => onChange({ ...value, [key]: next })}
            allowDefault
            defaultLabel={defaultLabel}
            defaultHint={hints?.[key] || undefined}
            fallbackValue={fallbacks?.[key] || undefined}
            aria-label={t(labelKey)}
          />
          <p className="mt-1.5 text-[11px] leading-[1.5] text-text-4">{t(captionKey)}</p>
        </div>
      ))}
      <p className="border-t border-hairline-soft pt-3 text-[11px] leading-[1.5] text-text-4">
        {t("text_tier_agent_boundary")}
      </p>
    </div>
  );
}
