import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { ProviderModelSelect } from "@/components/shared/ProviderModelSelect";
import { radioCardClass } from "@/components/shared/darkroom-tokens";
import type { NarrationDelivery } from "@/types";
import { voidCall } from "@/utils/async";

/** 项目的旁白交付配置（docs/adr/0089）：交付方式 + TTS 快照。 */
export interface NarrationDeliveryValue {
  delivery: NarrationDelivery;
  /** "provider/model"；空串表示未选。 */
  audioBackend: string;
  narrationVoice: string;
  /** null 表示不向供应商传语速。 */
  narrationSpeed: number | null;
}

export type NarrationDeliveryProblem = "model" | "voice";

/** TTS 配音项目必须带模型与音色；后期配音项目没有要求。 */
export function narrationDeliveryProblem(value: NarrationDeliveryValue): NarrationDeliveryProblem | null {
  if (value.delivery !== "use_tts") return null;
  if (!value.audioBackend) return "model";
  if (!value.narrationVoice.trim()) return "voice";
  return null;
}

/** 所选 TTS 模型是否支持配音语速；查询中、查询失败或未选模型时为 null。 */
function useTtsSpeedSupport(backend: string): boolean | null {
  const [answer, setAnswer] = useState<{ backend: string; supportsSpeed: boolean } | null>(null);
  useEffect(() => {
    if (!backend) return;
    const controller = new AbortController();
    voidCall((async () => {
      try {
        const res = await API.getTtsModelCapabilities(backend, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setAnswer({ backend, supportsSpeed: res.supports_speed });
      } catch {
        // 查不到能力时不置灰：语速仍由供应商自行决定是否生效
      }
    })());
    return () => controller.abort();
  }, [backend]);
  return answer?.backend === backend ? answer.supportsSpeed : null;
}

const FIELD_LABEL_CLS = "mb-1.5 block font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4";
const INPUT_CLS =
  "w-full rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[12.5px] text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50";

interface Props {
  value: NarrationDeliveryValue;
  onChange: (next: NarrationDeliveryValue) => void;
  audioBackends: string[];
  providerNames: Record<string, string>;
  modelNames: Record<string, string>;
}

export function NarrationDeliveryFields({ value, onChange, audioBackends, providerNames, modelNames }: Props) {
  const { t } = useTranslation("dashboard");
  const idBase = useId();
  const voiceId = `${idBase}-voice`;
  const speedId = `${idBase}-speed`;
  const supportsSpeed = useTtsSpeedSupport(value.delivery === "use_tts" ? value.audioBackend : "");
  const speedDisabled = supportsSpeed === false;
  const problem = narrationDeliveryProblem(value);

  return (
    <div className="space-y-4">
      <fieldset>
        <legend className="sr-only">{t("project_narration_delivery_title")}</legend>
        <div className="flex gap-2.5">
          {(["use_tts", "post_production"] as const).map((delivery) => (
            <label key={delivery} className={radioCardClass(value.delivery === delivery)}>
              <input
                type="radio"
                name={`${idBase}-delivery`}
                value={delivery}
                checked={value.delivery === delivery}
                onChange={() => onChange({ ...value, delivery })}
                className="sr-only"
              />
              <span>
                {delivery === "use_tts"
                  ? t("project_narration_delivery_use_tts")
                  : t("project_narration_delivery_post_production")}
              </span>
            </label>
          ))}
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-text-4">
          {value.delivery === "use_tts"
            ? t("project_narration_delivery_use_tts_desc")
            : t("project_narration_delivery_post_production_desc")}{" "}
          {t("project_narration_delivery_switch_hint")}
        </p>
      </fieldset>

      {value.delivery === "use_tts" && (
        <>
          <div>
            <div className={FIELD_LABEL_CLS}>{t("project_tts_model_label")}</div>
            <ProviderModelSelect
              value={value.audioBackend}
              options={audioBackends}
              providerNames={providerNames}
              modelNames={modelNames}
              onChange={(audioBackend) => onChange({ ...value, audioBackend })}
              aria-label={t("project_tts_model_label")}
            />
            {audioBackends.length === 0 ? (
              <p className="mt-1 text-[11px] text-warm">{t("project_tts_no_models")}</p>
            ) : problem === "model" ? (
              <p className="mt-1 text-[11px] text-warm">{t("project_tts_model_required")}</p>
            ) : null}
          </div>
          <div>
            <label htmlFor={voiceId} className={FIELD_LABEL_CLS}>
              {t("narration_voice_label")}
            </label>
            <input
              id={voiceId}
              type="text"
              value={value.narrationVoice}
              onChange={(e) => onChange({ ...value, narrationVoice: e.target.value })}
              aria-invalid={problem === "voice"}
              className={INPUT_CLS}
            />
            <p className={`mt-1 text-[11px] ${problem === "voice" ? "text-warm" : "text-text-4"}`}>
              {problem === "voice" ? t("project_narration_voice_required") : t("project_narration_voice_hint")}
            </p>
          </div>
          <div>
            <label htmlFor={speedId} className={FIELD_LABEL_CLS}>
              {t("narration_speed_label")}
            </label>
            <input
              id={speedId}
              type="number"
              min={0.1}
              step={0.1}
              value={value.narrationSpeed ?? ""}
              disabled={speedDisabled}
              aria-describedby={`${speedId}-hint`}
              onChange={(e) => {
                const raw = e.target.value;
                if (raw === "") {
                  onChange({ ...value, narrationSpeed: null });
                  return;
                }
                const next = Number(raw);
                // 仅过滤非有限数：NaN/Infinity 会被序列化为 null 误触「清除」语义；正数约束交由后端校验
                if (Number.isFinite(next)) onChange({ ...value, narrationSpeed: next });
              }}
              className={INPUT_CLS}
            />
            <p id={`${speedId}-hint`} className="mt-1 text-[11px] text-text-4">
              {speedDisabled ? t("project_narration_speed_unsupported") : t("narration_speed_hint")}
            </p>
          </div>
        </>
      )}
    </div>
  );
}
