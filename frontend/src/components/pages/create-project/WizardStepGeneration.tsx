import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { DurationTierPicker } from "@/components/shared/DurationTierPicker";
import { EpisodeTargetDurationField } from "@/components/shared/EpisodeTargetDurationField";
import { SpeechRateField } from "@/components/shared/SpeechRateField";
import { ModelConfigSection, type ModelConfigValue } from "@/components/shared/ModelConfigSection";
import { NarrationDeliveryFields, type NarrationDeliveryValue } from "@/components/shared/NarrationDeliveryFields";
import type { ProviderInfo } from "@/types";
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
  };
}

export interface WizardDurationValue {
  /** 广告项目的目标总时长（秒）；「自定义」输入无效时为 null。 */
  targetDuration: number | null;
  /** 单集目标时长（秒）；null = 不设目标。广告项目不使用。 */
  episodeTargetDuration: number | null;
  /** 口播语速估算（阅读单位 / 秒）；null = 按项目语言的默认值估算。 */
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
          {/* 项目还没有语言事实（由内容分析写入），单位按未知语言呈现 */}
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
        {/* 创建向导只暴露默认层（docs/adr/0054），按用途细分留给项目设置页 */}
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
            // 广告项目按目标总时长逐个分镜规划，不暴露默认时长
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
