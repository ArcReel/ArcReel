import { useId } from "react";
import { useTranslation } from "react-i18next";

import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { NarrationDeliveryFields, type NarrationDeliveryValue } from "@/components/shared/NarrationDeliveryFields";
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
  /** 全局默认的 TTS 设置：没有快照的项目切到 TTS 配音时用它预填。 */
  narrationDefaults: NarrationDefaultsResponse | null;
  audioBackends: string[];
  providerNames: Record<string, string>;
  modelNames: Record<string, string>;
}

/** 「配音」：旁白交付方式；角色声音绑定只在参考生视频项目出现。 */
export function VoiceTab({
  value,
  onChange,
  generationRoute,
  narrationDefaults,
  audioBackends,
  providerNames,
  modelNames,
}: VoiceTabProps) {
  const { t } = useTranslation("dashboard");
  const bindingId = useId();

  const handleNarrationChange = (next: NarrationDeliveryValue) => {
    onChange((prev) => {
      let narration = next;
      // 没有完整快照的项目切到 TTS 配音：缺的模型与音色、以及未设的语速按全局默认预填
      const hasSnapshot = next.audioBackend.includes("/") && next.narrationVoice.trim() !== "";
      if (next.delivery === "use_tts" && prev.narration.delivery !== "use_tts" && !hasSnapshot && narrationDefaults) {
        narration = {
          ...next,
          audioBackend: next.audioBackend.includes("/") ? next.audioBackend : (narrationDefaults.audio_backend ?? ""),
          narrationVoice: next.narrationVoice.trim() ? next.narrationVoice : narrationDefaults.narration_voice,
          narrationSpeed: next.narrationSpeed ?? narrationDefaults.narration_speed,
        };
      }
      return { ...prev, narration };
    });
  };

  return (
    <div className="flex flex-col gap-6">
      <TabHeader title={t("project_settings_tab_voice")} />

      {/* 旁白交付方式是每个项目的必填配置：任何内容模式的旁白单元都按它交付 */}
      <SettingsBlock title={t("project_narration_delivery_title")}>
        <NarrationDeliveryFields
          value={value.narration}
          onChange={handleNarrationChange}
          audioBackends={audioBackends}
          providerNames={providerNames}
          modelNames={modelNames}
        />
      </SettingsBlock>

      {/* 参考音频通道属于参考生视频路线；分镜图生视频路线上这项设置不改变任何交付内容，不展示也不写入 */}
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
