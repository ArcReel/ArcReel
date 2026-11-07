import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { DurationTierPicker } from "@/components/shared/DurationTierPicker";
import { EpisodeTargetDurationField } from "@/components/shared/EpisodeTargetDurationField";
import { SpeechRateField } from "@/components/shared/SpeechRateField";
import { ModelConfigSection, type ModelConfigValue } from "@/components/shared/ModelConfigSection";
import { NarrationDeliveryFields } from "@/components/shared/NarrationDeliveryFields";
import type { NarrationDeliveryValue } from "@/components/shared/narration-delivery";
import type { AutoBackendsResponse, ProviderInfo } from "@/types";
import type { CustomProviderInfo } from "@/types/custom-provider";
import { WizardSection } from "./WizardSection";

export interface WizardGenerationData {
  options: {
    video: string[];
    image: string[];
    text: string[];
    audio: string[];
    providerNames: Record<string, string>;
    modelNames: Record<string, string>;
  };
  providers: ProviderInfo[];
  customProviders: CustomProviderInfo[];
  globalDefaults: {
    video: string;
    videoI2V: string;
    videoR2V: string;
    image: string;
    imageT2I: string;
    imageI2I: string;
    textDefault: string;
    textSimple: string;
    textComplex: string;
    /** Backend-selected models when all layers are empty; absent on fetch failure. */
    auto?: AutoBackendsResponse | null;
  };
}

export interface WizardDurationValue {
  /** Ad total target seconds, null for invalid Custom input. */
  targetDuration: number | null;
  /** Episode target seconds; null means no target. Unused by ads. */
  episodeTargetDuration: number | null;
  /** Estimated speaking rate (reading units / second); null uses project-language defaults. */
  speechRate: number | null;
}

export interface WizardStepGenerationProps {
  isAd: boolean;
  usesReferenceImages: boolean;
  duration: WizardDurationValue;
  onDurationChange: (next: WizardDurationValue) => void;
  models: ModelConfigValue;
  onModelsChange: (next: ModelConfigValue) => void;
  narration: NarrationDeliveryValue;
  onNarrationChange: (next: NarrationDeliveryValue) => void;
  data: WizardGenerationData | null;
  error: string | null;
}

export function WizardStepGeneration({
  isAd,
  usesReferenceImages,
  duration,
  onDurationChange,
  models,
  onModelsChange,
  narration,
  onNarrationChange,
  data,
  error,
}: WizardStepGenerationProps) {
  const { t } = useTranslation(["dashboard", "common"]);

  return (
    <div className="flex flex-col gap-6">
      <WizardSection
        title={t("wizard_duration_title")}
        description={isAd ? t("wizard_duration_ad_desc") : t("wizard_duration_desc")}
      >
        <div className="flex flex-col gap-4">
          {isAd ? (
            <DurationTierPicker
              value={duration.targetDuration}
              onChange={(targetDuration) => onDurationChange({ ...duration, targetDuration })}
            />
          ) : (
            <EpisodeTargetDurationField
              value={duration.episodeTargetDuration}
              onChange={(episodeTargetDuration) => onDurationChange({ ...duration, episodeTargetDuration })}
            />
          )}
          {/* Projects lack language facts before content analysis, so show unknown-language units. */}
          <SpeechRateField
            value={duration.speechRate}
            onChange={(speechRate) => onDurationChange({ ...duration, speechRate })}
          />
        </div>
      </WizardSection>

      <WizardSection title={t("wizard_models_title")}>
        {!data && !error && (
          <p className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            {t("wizard_models_loading")}
          </p>
        )}
        {error && (
          <Alert variant="destructive">
            <AlertTitle>{t("wizard_models_load_failed")}</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {/* Wizards expose defaults only (docs/adr/0054), leaving purpose-specific tiers to project settings. */}
        {data && (
          <ModelConfigSection
            showSubFields={false}
            value={models}
            onChange={onModelsChange}
            providers={data.providers}
            customProviders={data.customProviders}
            options={{
              videoBackends: data.options.video,
              imageBackends: data.options.image,
              textBackends: data.options.text,
              providerNames: data.options.providerNames,
              modelNames: data.options.modelNames,
            }}
            globalDefaults={data.globalDefaults}
            usesReferenceImages={usesReferenceImages}
            // Ads plan individual shots from total target durations, without default-duration controls.
            enable={isAd ? { duration: false } : undefined}
          />
        )}
      </WizardSection>

      {data && (
        <WizardSection title={t("project_narration_delivery_title")}>
          <NarrationDeliveryFields
            value={narration}
            onChange={onNarrationChange}
            audioBackends={data.options.audio}
            providerNames={data.options.providerNames}
            modelNames={data.options.modelNames}
          />
        </WizardSection>
      )}
    </div>
  );
}
