import { useTranslation } from "react-i18next";
import { OptionalNumberField } from "@/components/ui/OptionalNumberField";

/**
 * 口播语速估算（阅读单位 / 秒）的项目级可选输入。
 *
 * 与旁白配音（TTS）的「语速」是两个东西：那个是供应商配音倍率，这个是把台词字数折算成
 * 秒数的估算速度，驱动时长下界指引、说话量提示与字幕定时。两者在界面上从不同栏位进入，
 * 措辞也各自独立，避免同名混淆。
 *
 * 单位名词随项目源语言：zh 计「字」、en / vi 计「词」——与后端 ``count_reading_units``
 * 的裁剪口径同源。语言未定时（创建向导阶段项目还没有 source_language）用中性的「字或词」
 * 并提示单位待定：具体名词在这里是错误承诺——按「字/秒」填入的数值，在语言被检测为 en / vi
 * 后会被同一个估算器按「词/秒」解释，数字不变而含义变了。
 */

/**
 * 硬区间（闭区间）：与后端 lib.speech_rate 的 is_valid_speech_rate 同一把尺。
 * 下界取值依据（下游时长换算的余量）见后端 MIN_SPEECH_RATE_UPS 的注释。
 */
const SPEECH_RATE_MIN = 0.001;
const SPEECH_RATE_MAX = 20;

/** 该值是否可提交（null = 未填，合法）。 */
export function isValidSpeechRate(value: number | null): boolean {
  if (value === null) return true;
  return value >= SPEECH_RATE_MIN && value <= SPEECH_RATE_MAX;
}

/** 阅读单位名词的 i18n key：en / vi 计「词」、zh 计「字」，语言未定时用中性的「字或词」。 */
function readingUnitKey(sourceLanguage?: string | null): string {
  const code = (sourceLanguage ?? "").trim().toLowerCase();
  if (code === "en" || code === "vi") return "reading_unit_word";
  if (code === "zh") return "reading_unit_char";
  return "reading_unit_generic";
}

/** 语言未定时单位名词无法确定，判据与 readingUnitKey 同源。 */
function isLanguagePending(sourceLanguage?: string | null): boolean {
  return readingUnitKey(sourceLanguage) === "reading_unit_generic";
}

export interface SpeechRateFieldProps {
  value: number | null;
  onChange: (next: number | null) => void;
  /** 项目 source_language（zh / en / vi）；创建阶段项目还没有语言事实，留空即可。 */
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
