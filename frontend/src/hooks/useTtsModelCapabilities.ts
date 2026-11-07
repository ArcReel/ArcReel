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
 * Selected TTS model's capabilities and voice list (provider/model); null while loading, on query failure or without a selected model.
 *
 * Get voices from the backend of the model's endpoint; different endpoints of one provider each have their own list. The backend localizes labels by Accept-Language;
 * refetch on language changes, retaining the previous result for the same model during refetch so the candidate list does not flash empty.
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
        // If capabilities are unavailable, do not disable or list voices: the provider still determines whether speech rate applies, and voices remain manually enterable
      }
    })());
    return () => controller.abort();
  }, [backend, language]);
  return answer?.backend === backend ? answer.capabilities : null;
}
