
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronRight } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { InlineWarning } from "@/components/shared/InlineWarning";
import { ModelLifecycleNotice, type ModelLifecycleNotices } from "@/components/shared/ModelLifecycleNotice";
import { ProviderModelSelect } from "@/components/shared/ProviderModelSelect";
import type { BucketDimension, FilteredCandidate, GenerationTypeBucket } from "@/types/system";

export function useGenerationTypeBucketLabels(): Record<GenerationTypeBucket, { label: string; caption?: string }> {
  const { t } = useTranslation("templates");
  return useMemo(
    () => ({
      t2i: { label: t("bucket_t2i_label") },
      i2i: { label: t("bucket_i2i_label"), caption: t("bucket_i2i_caption") },
      i2v: { label: t("bucket_i2v_label"), caption: t("bucket_i2v_caption") },
      r2v: { label: t("bucket_r2v_label"), caption: t("bucket_r2v_caption") },
    }),
    [t],
  );
}

export interface LayeredSubField {
  key: string;
  label: string;
  caption?: string;
  value: string;
  options: string[];
  effective?: string;
  auto?: string | null;
  bucket?: GenerationTypeBucket;
  filtered?: FilteredCandidate[];
  onChange: (next: string) => void;
}

export interface LayeredModelFieldsProps {
  defaultLabel: string;
  defaultValue: string;
  defaultOptions: string[];
  onDefaultChange: (next: string) => void;
  emptyLabel: string;
  emptyHint?: string;
  defaultEffective?: string;
  defaultAuto?: string | null;
  providerNames: Record<string, string>;
  modelNames?: Record<string, string>;
  renderOptionMeta?: (fullValue: string, subFieldKey?: string) => React.ReactNode;
  children?: React.ReactNode;
  subFields?: LayeredSubField[];
  subFieldsError?: {
    onRetry: () => void;
    retrying?: boolean;
  };
  footnote?: React.ReactNode;
  lifecycle?: ModelLifecycleNotices;
}

export function LayeredModelFields({
  defaultLabel,
  defaultValue,
  defaultOptions,
  onDefaultChange,
  emptyLabel,
  emptyHint,
  defaultEffective,
  defaultAuto,
  providerNames,
  modelNames,
  renderOptionMeta,
  children,
  subFields,
  subFieldsError,
  footnote,
  lifecycle,
}: LayeredModelFieldsProps) {
  const { t } = useTranslation(["templates", "common"]);
  const fields = subFields ?? [];
  const unmetReason = (unmet: BucketDimension[]) =>
    unmet.length > 0
      ? unmet.map((dimension) => t(`model_bucket_unmet_${dimension}`)).join(t("model_bucket_unmet_separator"))
      : t("model_bucket_unmet_unknown");
  const configuredCount = fields.filter((f) => !!f.value).length;
  const hasError = !!subFieldsError;

  const [open, setOpen] = useState(() => fields.some((f) => !!f.value) || hasError);

  const [prevHasError, setPrevHasError] = useState(hasError);
  if (hasError !== prevHasError) {
    setPrevHasError(hasError);
    if (hasError) setOpen(true);
  }

  return (
    <div className="flex flex-col gap-3">
      <ProviderModelSelect
        value={defaultValue}
        options={defaultOptions}
        providerNames={providerNames}
        modelNames={modelNames}
        onChange={onDefaultChange}
        allowDefault
        defaultLabel={emptyLabel}
        defaultHint={emptyHint}
        placeholder={emptyLabel}
        fallbackValue={defaultEffective}
        autoValue={defaultAuto}
        aria-label={defaultLabel}
        renderOptionMeta={renderOptionMeta}
      />
      <ModelLifecycleNotice
        value={defaultValue}
        notices={lifecycle}
        modelNames={modelNames}
        onSwitch={onDefaultChange}
      />

      {children}

      {(fields.length > 0 || hasError) && (
        <div className="border-t border-border pt-2">
          <Collapsible open={open} onOpenChange={setOpen}>
            <CollapsibleTrigger render={<Button variant="ghost" size="xs" className="-ml-2" />}>
              <ChevronRight aria-hidden className="transition-transform group-aria-expanded/button:rotate-90" />
              {t("model_bucket_section")}
              {configuredCount > 0 && (
                <Badge variant="secondary">{t("model_bucket_configured_count", { n: configuredCount })}</Badge>
              )}
            </CollapsibleTrigger>

            <CollapsibleContent className="mt-2">
              <div className="flex flex-col gap-4 border-l border-border pl-3">
                {subFieldsError ? (
                  <InlineWarning
                    message={t("model_bucket_candidates_error")}
                    action={{
                      label: t("common:retry"),
                      onClick: subFieldsError.onRetry,
                      disabled: subFieldsError.retrying,
                    }}
                  />
                ) : (
                  <p className="text-xs text-muted-foreground">{t("model_bucket_section_hint")}</p>
                )}
                {fields.map((field) => (
                  <div key={field.key} className="flex flex-col gap-1.5">
                    <span className="text-xs font-medium text-muted-foreground">{field.label}</span>
                    <ProviderModelSelect
                      value={field.value}
                      options={field.options}
                      providerNames={providerNames}
                      modelNames={modelNames}
                      onChange={field.onChange}
                      allowDefault
                      defaultLabel={t("follow_model_default")}
                      placeholder={t("follow_model_default")}
                      fallbackValue={field.effective}
                      fallbackLabel={t("follow_model_default")}
                      autoValue={field.auto}
                      aria-label={field.label}
                      renderOptionMeta={
                        renderOptionMeta && ((fullValue: string) => renderOptionMeta(fullValue, field.key))
                      }
                      unavailable={field.filtered?.map((item) => ({
                        value: item.option,
                        reason: unmetReason(item.unmet),
                      }))}
                      unavailableLabel={t("model_bucket_filtered_group", { purpose: field.label })}
                    />
                    <ModelLifecycleNotice
                      value={field.value}
                      notices={lifecycle}
                      layer={field.bucket}
                      modelNames={modelNames}
                      onSwitch={field.onChange}
                    />
                    {field.caption && <p className="text-xs text-muted-foreground">{field.caption}</p>}
                  </div>
                ))}
              </div>
            </CollapsibleContent>
          </Collapsible>
        </div>
      )}

      {footnote}
    </div>
  );
}
