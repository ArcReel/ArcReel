import { useId } from "react";
import { useTranslation } from "react-i18next";

import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import type { ModelLifecycleNotices } from "@/components/shared/ModelLifecycleNotice";
import { NarrationDeliveryFields } from "@/components/shared/NarrationDeliveryFields";
import type { NarrationDeliveryValue } from "@/components/shared/narration-delivery";
import type { CharacterVoiceBinding, NarrationDefaultsResponse } from "@/types";
import type { GenerationRoute } from "@/utils/generation-mode";

import type { ProjectSettingsForm } from "./project-settings-form";
import { SettingsBlock, TabHeader } from "./SettingsBlock";

const VOICE_BINDINGS: readonly { value: CharacterVoiceBinding; labelKey: string; descKey: string }[] = [
  {
    value: "prompt",
    labelKey: "character_voice_binding_prompt_label",
    descKey: "character_voice_binding_prompt_desc",
  },
  {
    value: "reference_audio",
    labelKey: "character_voice_binding_reference_audio_label",
    descKey: "character_voice_binding_reference_audio_desc",
  },
];

interface VoiceTabProps {
  value: ProjectSettingsForm;
  onChange: (update: (prev: ProjectSettingsForm) => ProjectSettingsForm) => void;
  generationRoute: GenerationRoute;
  narrationDefaults: NarrationDefaultsResponse | null;
  audioBackends: string[];
  providerNames: Record<string, string>;
  modelNames: Record<string, string>;
  lifecycle?: ModelLifecycleNotices;
}

export function VoiceTab({
  value,
  onChange,
  generationRoute,
  narrationDefaults,
  audioBackends,
  providerNames,
  modelNames,
  lifecycle,
}: VoiceTabProps) {
  const { t } = useTranslation("dashboard");
  const bindingId = useId();

  const handleNarrationChange = (next: NarrationDeliveryValue) => {
    onChange((prev) => ({ ...prev, narration: next }));
  };

  return (
    <div className="flex flex-col gap-6">
      <TabHeader title={t("project_settings_tab_voice")} />

      <SettingsBlock title={t("project_narration_delivery_title")}>
        <NarrationDeliveryFields
          defaults={narrationDefaults}
          value={value.narration}
          onChange={handleNarrationChange}
          audioBackends={audioBackends}
          providerNames={providerNames}
          modelNames={modelNames}
          lifecycle={lifecycle}
        />
      </SettingsBlock>

      {generationRoute === "reference_video" && (
        <SettingsBlock title={t("character_voice_binding_title")} titleId={bindingId}>
          <RadioGroup
            aria-labelledby={bindingId}
            value={value.voiceBinding}
            onValueChange={(next) => onChange((prev) => ({ ...prev, voiceBinding: next as CharacterVoiceBinding }))}
          >
            {VOICE_BINDINGS.map((option) => (
              <Label key={option.value} className="items-start">
                <RadioGroupItem value={option.value} className="mt-0.5" />
                <span className="flex flex-col gap-1">
                  {t(option.labelKey)}
                  <span className="max-w-[40em] text-xs font-normal text-muted-foreground">{t(option.descKey)}</span>
                </span>
              </Label>
            ))}
          </RadioGroup>
        </SettingsBlock>
      )}
    </div>
  );
}
