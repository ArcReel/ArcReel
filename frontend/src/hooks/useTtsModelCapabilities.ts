import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import type { TtsVoiceOption } from "@/types";
import { voidCall } from "@/utils/async";

export interface TtsModelCapabilities {
  supportsSpeed: boolean;
  voices: TtsVoiceOption[];
}

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
