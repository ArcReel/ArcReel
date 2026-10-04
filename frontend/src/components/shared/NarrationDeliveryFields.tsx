import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { cn } from "cn";

import { Input } from "@/components/ui/input";
import { ProviderModelSelect } from "@/components/shared/ProviderModelSelect";
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

const FIELD_LABEL_CLS = "text-xs font-medium text-muted-foreground";

/** 交付方式的单选卡片：整块可点，选中项用品牌色描边与浅底。 */
function deliveryCardClass(selected: boolean): string {
  return cn(
    "flex flex-1 cursor-pointer items-center justify-center rounded-lg border px-3 py-2.5 text-sm transition-colors has-focus-visible:ring-3 has-focus-visible:ring-ring/50",
    selected ? "border-primary/50 bg-primary/10 text-foreground" : "border-border text-subtle-foreground hover:text-foreground",
  );
}

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
    <div className="flex flex-col gap-4">
      <fieldset>
        <legend className="sr-only">{t("project_narration_delivery_title")}</legend>
        <div className="flex gap-2.5">
          {(["use_tts", "post_production"] as const).map((delivery) => (
            <label key={delivery} className={deliveryCardClass(value.delivery === delivery)}>
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
        <p className="mt-2 text-xs text-muted-foreground">
          {value.delivery === "use_tts"
            ? t("project_narration_delivery_use_tts_desc")
            : t("project_narration_delivery_post_production_desc")}{" "}
          {t("project_narration_delivery_switch_hint")}
        </p>
      </fieldset>

      {value.delivery === "use_tts" && (
        <>
          <div className="flex flex-col gap-1.5">
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
              <p className="text-xs text-warn">{t("project_tts_no_models")}</p>
            ) : problem === "model" ? (
              <p className="text-xs text-warn">{t("project_tts_model_required")}</p>
            ) : null}
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor={voiceId} className={FIELD_LABEL_CLS}>
              {t("narration_voice_label")}
            </label>
            <Input
              id={voiceId}
              type="text"
              value={value.narrationVoice}
              onChange={(e) => onChange({ ...value, narrationVoice: e.target.value })}
              aria-invalid={problem === "voice"}
            />
            <p className={cn("text-xs", problem === "voice" ? "text-warn" : "text-muted-foreground")}>
              {problem === "voice" ? t("project_narration_voice_required") : t("project_narration_voice_hint")}
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor={speedId} className={FIELD_LABEL_CLS}>
              {t("narration_speed_label")}
            </label>
            <Input
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
            />
            <p id={`${speedId}-hint`} className="text-xs text-muted-foreground">
              {speedDisabled ? t("project_narration_speed_unsupported") : t("narration_speed_hint")}
            </p>
          </div>
        </>
      )}
    </div>
  );
}
