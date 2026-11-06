import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import type { TtsVoiceOption } from "@/types";
import { voidCall } from "@/utils/async";

export interface TtsModelCapabilities {
  supportsSpeed: boolean;
  voices: TtsVoiceOption[];
}

/**
 * 所选 TTS 模型（provider/model）的能力与音色表；查询中、查询失败或未选模型时为 null。
 *
 * 音色表取自该模型所用调用端点的后端，同一供应商的不同端点各有一份。音色标签由后端按 Accept-Language
 * 成文，换语言即重取；重取期间沿用同一模型的上一份结果，不让候选列表闪空。
 */
export function useTtsModelCapabilities(backend: string): TtsModelCapabilities | null {
  const { i18n } = useTranslation();
  const language = i18n.language;
  const [answer, setAnswer] = useState<{ backend: string; capabilities: TtsModelCapabilities } | null>(null);
  useEffect(() => {
    if (!backend) return;
    const controller = new AbortController();
    voidCall((async () => {
      try {
        const res = await API.getTtsModelCapabilities(backend, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setAnswer({ backend, capabilities: { supportsSpeed: res.supports_speed, voices: res.voices } });
      } catch {
        // 查不到能力时不置灰、不列音色：语速仍由供应商自行决定是否生效，音色照常手填
      }
    })());
    return () => controller.abort();
  }, [backend, language]);
  return answer?.backend === backend ? answer.capabilities : null;
}
