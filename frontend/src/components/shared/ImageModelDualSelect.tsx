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
  /** Global default hint strings (show below the "use global default" option) */
  globalDefaultT2I?: string;
  globalDefaultI2I?: string;
}

export function ImageModelDualSelect({
  valueT2I,
  valueI2I,
  options,
  providerNames,
  onChange,
  globalDefaultT2I,
  globalDefaultI2I,
}: ImageModelDualSelectProps) {
  const { t } = useTranslation("templates");

  return (
    <div className="space-y-3">
      {/* T2I */}
      <div>
        <div className="mb-1 text-xs text-gray-400">{t("model_image_t2i")}</div>
        <ProviderModelSelect
          value={valueT2I}
          options={options}
          providerNames={providerNames}
          onChange={(next) => onChange({ t2i: next, i2i: valueI2I })}
          allowDefault
          defaultLabel={t("use_global_default")}
          defaultHint={
            globalDefaultT2I
              ? t("current_global_default", { value: globalDefaultT2I })
              : undefined
          }
          fallbackValue={globalDefaultT2I || undefined}
          aria-label={t("model_image_t2i")}
        />
      </div>

      {/* I2I */}
      <div>
        <div className="mb-1 text-xs text-gray-400">{t("model_image_i2i")}</div>
        <ProviderModelSelect
          value={valueI2I}
          options={options}
          providerNames={providerNames}
          onChange={(next) => onChange({ t2i: valueT2I, i2i: next })}
          allowDefault
          defaultLabel={t("use_global_default")}
          defaultHint={
            globalDefaultI2I
              ? t("current_global_default", { value: globalDefaultI2I })
              : undefined
          }
          fallbackValue={globalDefaultI2I || undefined}
          aria-label={t("model_image_i2i")}
        />
      </div>

      {/* Capability hint */}
      <p className="text-xs text-gray-500">{t("model_image_dual_hint")}</p>
    </div>
  );
}
