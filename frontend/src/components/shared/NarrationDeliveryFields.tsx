import { useId, useRef } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import type { NarrationDefaultsResponse } from "@/types";
import { Input } from "@/components/ui/input";
import { ModelLifecycleNotice, type ModelLifecycleNotices } from "@/components/shared/ModelLifecycleNotice";
import { NarrationVoiceInput } from "@/components/shared/NarrationVoiceInput";
import { ProviderModelSelect } from "@/components/shared/ProviderModelSelect";
import { useTtsModelCapabilities } from "@/hooks/useTtsModelCapabilities";
import { narrationDeliveryProblem, type NarrationDeliveryValue } from "./narration-delivery";

const FIELD_LABEL_CLS = "text-xs font-medium text-muted-foreground";

function deliveryCardClass(selected: boolean): string {
  return cn(
    "flex flex-1 cursor-pointer items-center justify-center rounded-lg border px-3 py-2.5 text-sm transition-colors has-focus-visible:ring-3 has-focus-visible:ring-ring/50",
    selected ? "border-primary/50 bg-primary/10 text-foreground" : "border-border text-subtle-foreground hover:text-foreground",
  );
}


interface Props {
  defaults?: NarrationDefaultsResponse | null;
  value: NarrationDeliveryValue;
  onChange: (next: NarrationDeliveryValue) => void;
  audioBackends: string[];
  providerNames: Record<string, string>;
  modelNames: Record<string, string>;
  lifecycle?: ModelLifecycleNotices;
}

function DeliverySelector({ delivery, onChange }: {
  delivery: NarrationDeliveryValue["delivery"];
  onChange: (delivery: NarrationDeliveryValue["delivery"]) => void;
}) {
  const { t } = useTranslation("dashboard");
  const idBase = useId();
  return (
    <fieldset>
      <legend className="sr-only">{t("project_narration_delivery_title")}</legend>
      <div className="flex gap-2.5">
        {(["use_tts", "post_production"] as const).map((option) => (
          <label key={option} className={deliveryCardClass(option === delivery)}>
            <input
              type="radio"
              name={`${idBase}-delivery`}
              value={option}
              checked={option === delivery}
              onChange={() => onChange(option)}
              className="sr-only"
            />
            <span>
              {option === "use_tts"
                ? t("project_narration_delivery_use_tts")
                : t("project_narration_delivery_post_production")}
            </span>
          </label>
        ))}
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        {delivery === "use_tts"
          ? t("project_narration_delivery_use_tts_desc")
          : t("project_narration_delivery_post_production_desc")}{" "}
        {t("project_narration_delivery_switch_hint")}
      </p>
    </fieldset>
  );
}

function NarrationSettingsSource({ value, defaults, onChange }: Pick<Props, "value" | "defaults" | "onChange">) {
  const { t } = useTranslation("dashboard");
  const idBase = useId();
  const summary = value.override
    ? [value.audioBackend, value.narrationVoice, value.narrationSpeed]
    : [defaults?.audio_backend, defaults?.narration_voice, defaults?.narration_speed];
  return (
    <>
      {value.delivery === "use_tts" && (
        <fieldset className="flex gap-2.5">
          <legend className="sr-only">{t("project_tts_source_label")}</legend>
          {([false, true] as const).map((override) => (
            <label key={String(override)} className={deliveryCardClass(value.override === override)}>
              <input
                type="radio"
                className="sr-only"
                name={`${idBase}-override`}
                checked={value.override === override}
                onChange={() => {
                  onChange({
                    ...value,
                    override,
                    audioBackend: override ? defaults?.audio_backend ?? "" : "",
                    narrationVoice: override ? defaults?.narration_voice ?? "" : "",
                    narrationSpeed: override ? defaults?.narration_speed ?? null : null,
                  });
                }}
              />
              {t(override ? "project_tts_override" : "project_tts_inherit")}
            </label>
          ))}
        </fieldset>
      )}
      {value.delivery === "post_production" && (
        <p className={FIELD_LABEL_CLS}>{t(value.override ? "project_tts_override" : "project_tts_inherit")}</p>
      )}
      {(!value.override || value.delivery === "post_production") && (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-xs">
          {(["project_tts_model_label", "narration_voice_label", "narration_speed_label"] as const).map((label, i) => (
            <div className="contents" key={label}>
              <dt>{t(label)}</dt>
              <dd className="break-words">{summary[i] || "—"}</dd>
            </div>
          ))}
        </dl>
      )}
    </>
  );
}

export function NarrationDeliveryFields({ defaults, value, onChange, audioBackends, providerNames, modelNames, lifecycle }: Props) {
  const { t } = useTranslation("dashboard");
  const idBase = useId();
  const voiceId = `${idBase}-voice`;
  const speedId = `${idBase}-speed`;
  const pendingModel = useRef<string | null>(null);
  const capabilities = useTtsModelCapabilities(value.delivery === "use_tts" ? value.audioBackend : "", (backend, profile) => {
    if (pendingModel.current !== backend || value.audioBackend !== backend) return;
    pendingModel.current = null;
    const first = profile.voices[0]?.id;
    if (first && !value.narrationVoice) onChange({ ...value, narrationVoice: first });
  });
  const speedDisabled = capabilities?.supportsSpeed === false;
  const voices = capabilities?.voices ?? [];
  const changeModel = (audioBackend: string) => {
    if (audioBackend === value.audioBackend) return;
    pendingModel.current = audioBackend;
    onChange({ ...value, audioBackend, narrationVoice: "", narrationSpeed: null });
  };
  const problem = narrationDeliveryProblem(value);

  return (
    <div className="flex flex-col gap-4">
      <DeliverySelector delivery={value.delivery} onChange={(delivery) => onChange({ ...value, delivery })} />

      <NarrationSettingsSource
        value={value}
        defaults={defaults}
        onChange={(next) => {
          pendingModel.current = null;
          onChange(next);
        }}
      />
      {value.override && (value.delivery === "use_tts" || problem) && (
        <>
          <div className="flex flex-col gap-1.5">
            <div className={FIELD_LABEL_CLS}>{t("project_tts_model_label")}</div>
            <ProviderModelSelect
              value={value.audioBackend}
              options={audioBackends}
              providerNames={providerNames}
              modelNames={modelNames}
              onChange={changeModel}
              aria-label={t("project_tts_model_label")}
            />
            <ModelLifecycleNotice
              value={value.audioBackend}
              notices={lifecycle}
              modelNames={modelNames}
              onSwitch={changeModel}
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
              onChange={(narrationVoice) => {
                pendingModel.current = null;
                onChange({ ...value, narrationVoice });
              }}
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
              disabled={speedDisabled || !value.narrationVoice.trim()}
              aria-describedby={`${speedId}-hint`}
              onChange={(e) => {
                const raw = e.target.value;
                if (raw === "") {
                  onChange({ ...value, narrationSpeed: null });
                  return;
                }
                const next = Number(raw);
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
