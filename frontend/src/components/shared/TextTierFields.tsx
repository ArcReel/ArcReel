import { useTranslation } from "react-i18next";
import { LayeredModelFields, type LayeredSubField } from "./LayeredModelFields";
import type { ModelLifecycleNotices } from "./ModelLifecycleNotice";

export interface TextTierValue {
  default: string;
  simple: string;
  complex: string;
}

const SUB_TIERS = [
  { key: "simple", labelKey: "text_tier_simple_label", captionKey: "text_tier_simple_caption" },
  { key: "complex", labelKey: "text_tier_complex_label", captionKey: "text_tier_complex_caption" },
] as const;

export interface TextTierFieldsProps {
  value: TextTierValue;
  onChange: (next: TextTierValue) => void;
  options: string[];
  providerNames: Record<string, string>;
  modelNames?: Record<string, string>;
  defaultLabel: string;
  defaultHint?: string;
  fallbacks?: Partial<TextTierValue>;
  auto?: string | null;
  showTiers?: boolean;
  lifecycle?: ModelLifecycleNotices;
}

export function TextTierFields({
  value,
  onChange,
  options,
  providerNames,
  modelNames,
  defaultLabel,
  defaultHint,
  fallbacks,
  auto,
  showTiers = true,
  lifecycle,
}: TextTierFieldsProps) {
  const { t } = useTranslation("templates");

  const subFields: LayeredSubField[] = SUB_TIERS.map(({ key, labelKey, captionKey }) => ({
    key,
    label: t(labelKey),
    caption: t(captionKey),
    value: value[key],
    options,
    effective: fallbacks?.[key] || undefined,
    auto,
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
      defaultAuto={auto}
      providerNames={providerNames}
      modelNames={modelNames}
      subFields={showTiers ? subFields : undefined}
      lifecycle={lifecycle}
      footnote={
        <p className="border-t border-border pt-3 text-xs text-muted-foreground">
          {t("text_tier_agent_boundary")}
        </p>
      }
    />
  );
}
