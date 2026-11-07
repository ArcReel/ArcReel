import { useTranslation } from "react-i18next";
import { LayeredModelFields, type LayeredSubField } from "./LayeredModelFields";
import type { ModelLifecycleNotices } from "./ModelLifecycleNotice";

/** Text-task tier values (docs/adr/0051). Field names are tier keys: callers hardcode categorization; users configure per-tier backends only. */
export interface TextTierValue {
  default: string;
  simple: string;
  complex: string;
}

/** Subtiers within collapsible regions; Default model is the default layer, excluded here. */
const SUB_TIERS = [
  { key: "simple", labelKey: "text_tier_simple_label", captionKey: "text_tier_simple_caption" },
  { key: "complex", labelKey: "text_tier_complex_label", captionKey: "text_tier_complex_caption" },
] as const;

export interface TextTierFieldsProps {
  value: TextTierValue;
  onChange: (next: TextTierValue) => void;
  options: string[];
  providerNames: Record<string, string>;
  /** "provider/model" → current-language model names, sharing `providerNames` sources; missing keys fall back to model IDs. */
  modelNames?: Record<string, string>;
  /** Empty default-tier labels: Automatic globally, Use global default in projects/wizards. */
  defaultLabel: string;
  /** Secondary hints next to empty default-tier options, e.g. Automatic globally. */
  defaultHint?: string;
  /**
   * Effective values for empty tiers, precomputed through project-priority resolution chains, in provider/model form.
   * Triggers show Follow…·effective value to expose inheritance results. Global default tiers omit this because they are the baseline.
   */
  fallbacks?: Partial<TextTierValue>;
  /**
   * Auto-selected text models when tiers/all inheritance sources are empty (identical inference across tiers); null means no usable providers.
   * Display only when tier `fallbacks` are absent; `fallbacks` must already resolve through global default models.
   */
  auto?: string | null;
  /** Omission renders default tiers only; creation wizards expose only default layers (docs/adr/0054). */
  showTiers?: boolean;
  /** Retirement notices from candidate `lifecycle`; all tiers use default-layer switch targets because text models have no task-type buckets. */
  lifecycle?: ModelLifecycleNotices;
}

/**
 * Shared text-tier configuration: default-model selects always show; Simple/Complex tasks sit in Specify models by use
 * collapsible regions. Persistent tier explanations list covered call sites; card footers state Agent-provider boundaries. Global settings/project settings/
 * creation wizards reuse this, ensuring one source for coverage copy. Component presentation never affects tier resolution.
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
