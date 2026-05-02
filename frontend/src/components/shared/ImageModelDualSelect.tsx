/**
 * ImageModelDualSelect — dual image model selector for T2I and I2I.
 *
 * Renders two side-by-side ProviderModelSelect dropdowns, one for
 * Text-to-Image (T2I) and one for Image-to-Image (I2I).
 *
 * NOTE: All image backend options are shown in both dropdowns without
 * capability filtering.  We intentionally avoid filtering by T2I/I2I
 * capability here because resolving a `provider/model` string to its
 * endpoint capabilities requires a catalog lookup that isn't always
 * available at this layer.  Backend gating at generation time handles
 * mismatches.  The hint text below guides the user to pick sensible models.
 *
 * Label / hint 字符串通过 prop 注入而非内部 t()，使本组件可在 project 层
 * （命名空间 templates）与 system settings 层（命名空间 dashboard）共用。
 */

import { useTranslation } from "react-i18next";
import { ProviderModelSelect } from "@/components/ui/ProviderModelSelect";

export interface ImageModelDualSelectProps {
  /** Current T2I value — empty string means "follow global default" */
  valueT2I: string;
  /** Current I2I value — empty string means "follow global default" */
  valueI2I: string;
  /** Available backend strings like "gemini/imagen-4" */
  options: string[];
  providerNames: Record<string, string>;
  /** Called when either slot changes */
  onChange: (next: { t2i: string; i2i: string }) => void;
  /** 行内 label 覆盖（已 t()）；不传则使用 templates 命名空间默认值 */
  labelT2I?: string;
  labelI2I?: string;
  /** 「跟随全局默认 / 自动选择」label（已 t()）；不传则使用 templates 默认值 */
  defaultLabel?: string;
  /** 「跟随全局默认」下方的提示文（已 t()）；与 globalDefault* 互斥 */
  defaultHint?: string;
  /** 显示「当前全局默认 = X」回退提示；仅 project 层有上级 default 可参考 */
  globalDefaultT2I?: string;
  globalDefaultI2I?: string;
  /** 是否显示底部 capability hint，默认 true（系统设置层可关掉） */
  showCapabilityHint?: boolean;
}

export function ImageModelDualSelect({
  valueT2I,
  valueI2I,
  options,
  providerNames,
  onChange,
  labelT2I,
  labelI2I,
  defaultLabel,
  defaultHint,
  globalDefaultT2I,
  globalDefaultI2I,
  showCapabilityHint = true,
}: ImageModelDualSelectProps) {
  const { t } = useTranslation("templates");

  const t2iLabel = labelT2I ?? t("model_image_t2i");
  const i2iLabel = labelI2I ?? t("model_image_i2i");
  const fallbackLabel = defaultLabel ?? t("use_global_default");
  const t2iHint = globalDefaultT2I
    ? t("current_global_default", { value: globalDefaultT2I })
    : defaultHint;
  const i2iHint = globalDefaultI2I
    ? t("current_global_default", { value: globalDefaultI2I })
    : defaultHint;

  return (
    <div className="space-y-3">
      {/* T2I */}
      <div>
        <div className="mb-1 text-xs text-gray-400">{t2iLabel}</div>
        <ProviderModelSelect
          value={valueT2I}
          options={options}
          providerNames={providerNames}
          onChange={(next) => onChange({ t2i: next, i2i: valueI2I })}
          allowDefault
          defaultLabel={fallbackLabel}
          defaultHint={t2iHint}
          fallbackValue={globalDefaultT2I || undefined}
          aria-label={t2iLabel}
        />
      </div>

      {/* I2I */}
      <div>
        <div className="mb-1 text-xs text-gray-400">{i2iLabel}</div>
        <ProviderModelSelect
          value={valueI2I}
          options={options}
          providerNames={providerNames}
          onChange={(next) => onChange({ t2i: valueT2I, i2i: next })}
          allowDefault
          defaultLabel={fallbackLabel}
          defaultHint={i2iHint}
          fallbackValue={globalDefaultI2I || undefined}
          aria-label={i2iLabel}
        />
      </div>

      {showCapabilityHint && (
        <p className="text-xs text-gray-500">{t("model_image_dual_hint")}</p>
      )}
    </div>
  );
}
