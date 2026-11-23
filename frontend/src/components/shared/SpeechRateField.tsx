import { useTranslation } from "react-i18next";
import { OptionalNumberField } from "@/components/shared/OptionalNumberField";
import { SPEECH_RATE_MAX, SPEECH_RATE_MIN, isValidSpeechRate } from "./speech-rate";


function readingUnitKey(sourceLanguage?: string | null): string {
  const code = (sourceLanguage ?? "").trim().toLowerCase();
  if (code === "en" || code === "vi" || code === "ru") return "reading_unit_word";
  if (code === "zh") return "reading_unit_char";
  return "reading_unit_generic";
}

function isLanguagePending(sourceLanguage?: string | null): boolean {
  return readingUnitKey(sourceLanguage) === "reading_unit_generic";
}

export interface SpeechRateFieldProps {
  value: number | null;
  onChange: (next: number | null) => void;
  sourceLanguage?: string | null;
}

export function SpeechRateField({ value, onChange, sourceLanguage }: SpeechRateFieldProps) {
  const { t } = useTranslation("dashboard");

  return (
    <OptionalNumberField
      label={t("speech_rate_label")}
      value={value}
      onChange={onChange}
      unit={t(`${readingUnitKey(sourceLanguage)}_per_second`)}
      hint={`${t("speech_rate_hint")}${
        isLanguagePending(sourceLanguage) ? ` ${t("speech_rate_hint_language_pending")}` : ""
      }`}
      errorMessage={t("speech_rate_out_of_range", { min: SPEECH_RATE_MIN, max: SPEECH_RATE_MAX })}
      invalid={!isValidSpeechRate(value)}
      idSuffix="speech-rate"
      inputMode="decimal"
      max={SPEECH_RATE_MAX}
      step="any"
    />
  );
}
