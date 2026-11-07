import { useTranslation } from "react-i18next";
import { OptionalNumberField } from "@/components/shared/OptionalNumberField";
import { SPEECH_RATE_MAX, SPEECH_RATE_MIN, isValidSpeechRate } from "./speech-rate";

/**
 * Optional project-level estimated speech rate (reading units/second).
 *
 * Distinct from narration TTS speed: that is provider speech multipliers, while this estimate converts dialogue reading-unit counts into
 * seconds, driving minimum-duration guidance, speech-volume hints and subtitle timing. UI entry points are separate,
 * with independent wording to avoid same-name confusion.
 *
 * Unit nouns follow source languages: zh counts characters; en/vi/ru count words, sharing backend ``count_reading_units``
 * segmentation criteria. Unknown languages (creation wizards before source_language exists) use neutral Characters or words,
 * explaining pending units: concrete nouns would promise incorrectly. Values entered as characters/second would, after language detection as en/vi/ru,
 * be interpreted by the same estimator as words/second, changing meaning without changing numbers.
 */

/** Reading-unit noun i18n keys: words for en/vi/ru, characters for zh, neutral Characters or words when languages are unknown. */
function readingUnitKey(sourceLanguage?: string | null): string {
  const code = (sourceLanguage ?? "").trim().toLowerCase();
  if (code === "en" || code === "vi" || code === "ru") return "reading_unit_word";
  if (code === "zh") return "reading_unit_char";
  return "reading_unit_generic";
}

/** Unknown languages prevent choosing unit nouns; share criteria with readingUnitKey. */
function isLanguagePending(sourceLanguage?: string | null): boolean {
  return readingUnitKey(sourceLanguage) === "reading_unit_generic";
}

export interface SpeechRateFieldProps {
  value: number | null;
  onChange: (next: number | null) => void;
  /** Project source_language (zh/en/vi/ru); leave empty during creation before language facts exist. */
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
