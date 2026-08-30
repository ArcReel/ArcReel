import { useTranslation } from "react-i18next";
import { LayeredModelFields, type LayeredSubField } from "./LayeredModelFields";

/** 文本任务档位取值（docs/adr/0051）。字段名即档位键：调用点在代码里固定归档，用户只配置每档 backend。 */
export interface TextTierValue {
  default: string;
  simple: string;
  complex: string;
}

/** 折叠区内的细分档位；「默认模型」是默认层、不在其中。 */
const SUB_TIERS = [
  { key: "simple", labelKey: "text_tier_simple_label", captionKey: "text_tier_simple_caption" },
  { key: "complex", labelKey: "text_tier_complex_label", captionKey: "text_tier_complex_caption" },
] as const;

export interface TextTierFieldsProps {
  value: TextTierValue;
  onChange: (next: TextTierValue) => void;
  options: string[];
  providerNames: Record<string, string>;
  /** "provider/model" → 当前语言下的模型名，与 `providerNames` 同源；缺键退回 model id。 */
  modelNames?: Record<string, string>;
  /** 默认档空选项的标签：全局设置为「自动选择」，项目/向导为「使用全局默认」。 */
  defaultLabel: string;
  /** 默认档空选项旁的次要提示（如全局设置的「自动」）。 */
  defaultHint?: string;
  /**
   * 各档留空时的实际生效值（按项目优先解析链算好后传入，格式 provider/model）。
   * 触发按钮以「跟随…· 生效值」呈现，让用户看到继承结果。全局设置的默认档不传（它即基准）。
   */
  fallbacks?: Partial<TextTierValue>;
  /** 省略即只渲染默认档——创建向导只暴露默认层（docs/adr/0054）。 */
  showTiers?: boolean;
}

/**
 * 文本档位配置的同源组件：默认模型主下拉常驻，简单任务 / 复杂任务两档收在「按用途指定模型」
 * 折叠区内，每档常驻说明列出覆盖调用点，卡片底部注明 Agent 供应商边界。全局设置、项目设置、
 * 创建向导三处复用，保证覆盖范围文案单一真相源。档位的解析行为不受本组件形态影响。
 */
export function TextTierFields({
  value,
  onChange,
  options,
  providerNames,
  modelNames,
  defaultLabel,
  defaultHint,
  fallbacks,
  showTiers = true,
}: TextTierFieldsProps) {
  const { t } = useTranslation("templates");

  const subFields: LayeredSubField[] = SUB_TIERS.map(({ key, labelKey, captionKey }) => ({
    key,
    label: t(labelKey),
    caption: t(captionKey),
    value: value[key],
    options,
    effective: fallbacks?.[key] || undefined,
    onChange: (next: string) => onChange({ ...value, [key]: next }),
  }));

  return (
    <LayeredModelFields
      defaultLabel={t("text_tier_default_label")}
      defaultValue={value.default}
      defaultOptions={options}
      onDefaultChange={(next) => onChange({ ...value, default: next })}
      emptyLabel={defaultLabel}
      emptyHint={defaultHint}
      defaultEffective={fallbacks?.default || undefined}
      providerNames={providerNames}
      modelNames={modelNames}
      subFields={showTiers ? subFields : undefined}
      footnote={
        <p className="border-t border-hairline-soft pt-3 text-[11px] leading-[1.5] text-text-4">
          {t("text_tier_agent_boundary")}
        </p>
      }
    />
  );
}
