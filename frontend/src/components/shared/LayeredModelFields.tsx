/**
 * LayeredModelFields: shared Default model + Specify models by use interactions (docs/adr/0054).
 *
 * Shared by text tiers, image task-type buckets and video task-type buckets: persistent primary default selects with initially collapsed
 * subdivisions beneath. Global/project settings reuse components; creation wizards omit subFields, leaving default layers only.
 *
 * Empty subdivisions show fully resolved effective models on triggers, exposing actual execution models without expansion;
 * all-empty layers show backend automatic selections. Callers precompute resolution by layer keys; components know no layer hierarchy.
 *
 * UI copy consistently says Specify models by use, never Capability/Bucket (see GLOSSARY.md task-type bucket).
 */

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

/** Shared task-type bucket UI labels/coverage explanations for image/video callers. */
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
  /** Translated t() subdivision labels named after generation paths (text-to-image/image-to-image/image-to-video/reference-to-video). */
  label: string;
  /** Translated t() coverage explanations beneath selects, supplied only when labels do not explain coverage themselves. */
  caption?: string;
  value: string;
  /** Subdivision candidates filtered by capabilities at subdivision layers. */
  options: string[];
  /** Effective models when empty (provider/model); omit when unresolved. */
  effective?: string;
  /** Automatic models when all layers are empty, matching `ProviderModelSelect` `autoValue` semantics. */
  auto?: string | null;
  /** Subdivision task-type buckets determine retirement-switch candidates; text tiers omit this and use default layers. */
  bucket?: GenerationTypeBucket;
  /** Default-layer models absent here for inadequate capabilities, with candidate `filtered` reasons, listed last and unselectable. */
  filtered?: FilteredCandidate[];
  onChange: (next: string) => void;
}

export interface LayeredModelFieldsProps {
  /** Default-select accessible labels; outer card titles provide visual labels. */
  defaultLabel: string;
  defaultValue: string;
  /** Unfiltered default candidates: default layers promise no capabilities; resolution gates reject inadequate capabilities. */
  defaultOptions: string[];
  onDefaultChange: (next: string) => void;
  /** Empty default-option labels: Automatic globally, Use global default for projects. */
  emptyLabel: string;
  emptyHint?: string;
  /** Effective empty-default models (project layer = global default); global layers are baselines and omit this. */
  defaultEffective?: string;
  /**
   * Auto-selected models when default layers/all inheritance sources are empty, matching `ProviderModelSelect` `autoValue`.
   * Supply only without inheritance beyond `defaultEffective`: any nonempty inherited layer prevents automatic selection.
   */
  defaultAuto?: string | null;
  providerNames: Record<string, string>;
  /** "provider/model" → current-language names sharing `providerNames` sources; missing keys fall back to model IDs. */
  modelNames?: Record<string, string>;
  /**
   * Supplementary option-row information. Second arguments are originating subdivision `key` values; defaults omit them because they span all uses
   * without single generation paths. Default/subdivision selects share renderers; route path-dependent values by this argument,
   * otherwise one select path would incorrectly apply to all selects.
   */
  renderOptionMeta?: (fullValue: string, subFieldKey?: string) => React.ReactNode;
  /** Additional contents between default selects/collapsible regions (model specs/resolution/duration, etc.). */
  children?: React.ReactNode;
  /** Subdivisions; omitted/empty arrays hide collapsible regions (creation wizards expose defaults only). */
  subFields?: LayeredSubField[];
  /**
   * Subdivision-candidate fetch failures: nonempty values render collapsible regions even without subdivisions, with error copy/
   * Retry. Components own copy and callers pass retry callbacks only, keeping wording identical across both call sites.
   *
   * Fetch failures only; omit while loading or after empty success. Those cases render collapsible regions according to
   * `subFields` presence, without error narratives.
   */
  subFieldsError?: {
    onRetry: () => void;
    /** Retries pending; disable buttons to provide feedback during slow responses. */
    retrying?: boolean;
  };
  /** Persistent explanations after collapsible regions (e.g. text-tier Agent boundaries). */
  footnote?: React.ReactNode;
  /** Retirement notices from candidate `lifecycle`, beneath default/subdivision selects when selected models retire or have retired. */
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

  // Initially collapsed unless subdivisions are already configured; otherwise active overrides would be invisible,
  // misleading users into expecting default-model edits to apply immediately. Users control opening after mount.
  const [open, setOpen] = useState(() => fields.some((f) => !!f.value) || hasError);

  // Fetch failures usually arrive asynchronously after mount, too late for initial open state;
  // force expansion once when errors first appear, exposing failures without requiring manual expansion. Then permit
  // manual collapse without repeatedly reopening while hasError remains true; Retry already provides next steps.
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
        // With all-empty layers and absent automatic-selection previews, triggers fall back to placeholders. Show empty-value semantics
        // (Automatic/Use global default), otherwise generic Select model would wrongly imply required selection.
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
