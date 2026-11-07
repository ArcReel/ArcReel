import { useId } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { Input } from "@/components/ui/input";
import { ModelLifecycleNotice, type ModelLifecycleNotices } from "@/components/shared/ModelLifecycleNotice";
import { NarrationVoiceInput } from "@/components/shared/NarrationVoiceInput";
import { ProviderModelSelect } from "@/components/shared/ProviderModelSelect";
import { useTtsModelCapabilities } from "@/hooks/useTtsModelCapabilities";
import { narrationDeliveryProblem, type NarrationDeliveryValue } from "./narration-delivery";

const FIELD_LABEL_CLS = "text-xs font-medium text-muted-foreground";

/** Delivery-method radio cards: the entire card is clickable; selected cards use a brand-color outline and light background. */
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
  /** Retirement notice from candidates' `lifecycle`; show below the dropdown when the selected voiceover model is retiring or retired. */
  lifecycle?: ModelLifecycleNotices;
}

export function NarrationDeliveryFields({ value, onChange, audioBackends, providerNames, modelNames, lifecycle }: Props) {
  const { t } = useTranslation("dashboard");
  const idBase = useId();
  const voiceId = `${idBase}-voice`;
  const speedId = `${idBase}-speed`;
  const capabilities = useTtsModelCapabilities(value.delivery === "use_tts" ? value.audioBackend : "");
  const speedDisabled = capabilities?.supportsSpeed === false;
  const voices = capabilities?.voices ?? [];
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
            <ModelLifecycleNotice
              value={value.audioBackend}
              notices={lifecycle}
              modelNames={modelNames}
              onSwitch={(audioBackend) => onChange({ ...value, audioBackend })}
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
            <NarrationVoiceInput
              id={voiceId}
              value={value.narrationVoice}
              onChange={(narrationVoice) => onChange({ ...value, narrationVoice })}
              voices={voices}
              aria-invalid={problem === "voice"}
            />
            <p className={cn("text-xs", problem === "voice" ? "text-warn" : "text-muted-foreground")}>
              {problem === "voice"
                ? t("project_narration_voice_required")
                : voices.length > 0
                  ? t("project_narration_voice_hint_with_voices")
                  : t("project_narration_voice_hint")}
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
                // Filter only nonfinite numbers: NaN/Infinity serialize as null and accidentally clear values; leave positive-number validation to the backend
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
