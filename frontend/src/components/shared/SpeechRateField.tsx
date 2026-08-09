import { useId } from "react";
import { useTranslation } from "react-i18next";
import { FieldLabel } from "@/components/ui/FieldLabel";

/**
 * 口播语速估算（阅读单位 / 秒）的项目级可选输入。
 *
 * 与旁白配音（TTS）的「语速」是两个东西：那个是供应商配音倍率，这个是把台词字数折算成
 * 秒数的估算速度，驱动时长下界指引、说话量提示与字幕定时。两者在界面上从不同栏位进入，
 * 措辞也各自独立，避免同名混淆。
 *
 * 单位名词随项目源语言：zh 计「字」、en / vi 计「词」——与后端 ``count_reading_units``
 * 的裁剪口径同源。创建项目时项目尚无语言事实，按未知语言（计字）呈现。
 */

/** 硬区间：与后端 lib.speech_rate 的 is_valid_speech_rate 同一把尺。 */
export const SPEECH_RATE_MIN_EXCLUSIVE = 0;
export const SPEECH_RATE_MAX = 20;

/** 该值是否可提交（null = 未填，合法）。 */
export function isValidSpeechRate(value: number | null): boolean {
  if (value === null) return true;
  return Number.isFinite(value) && value > SPEECH_RATE_MIN_EXCLUSIVE && value <= SPEECH_RATE_MAX;
}

/** 阅读单位名词的 i18n key：按词计的语言用「词」，其余（含未知 / 未定）按「字」。 */
export function readingUnitKey(sourceLanguage?: string | null): string {
  const code = (sourceLanguage ?? "").trim().toLowerCase();
  return code === "en" || code === "vi" ? "reading_unit_word" : "reading_unit_char";
}

export interface SpeechRateFieldProps {
  value: number | null;
  onChange: (next: number | null) => void;
  /** 项目 source_language（zh / en / vi）；创建阶段项目还没有语言事实，留空即可。 */
  sourceLanguage?: string | null;
}

export function SpeechRateField({ value, onChange, sourceLanguage }: SpeechRateFieldProps) {
  const { t } = useTranslation("dashboard");
  const id = `${useId()}-speech-rate`;
  const errorId = `${id}-error`;
  const unit = t(`${readingUnitKey(sourceLanguage)}_per_second`);
  const invalid = !isValidSpeechRate(value);

  return (
    <div>
      <FieldLabel htmlFor={id}>{t("speech_rate_label")}</FieldLabel>
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="number"
          inputMode="decimal"
          min={0.1}
          max={SPEECH_RATE_MAX}
          step={0.1}
          value={value ?? ""}
          aria-invalid={invalid || undefined}
          aria-describedby={invalid ? errorId : undefined}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") {
              onChange(null);
              return;
            }
            const next = Number(raw);
            // 只挡非有限数（NaN / Infinity 会被序列化成 null，误触「清除」语义）；
            // 区间校验交给下面的行内提示与后端，输入过程中不吞用户的按键
            if (Number.isFinite(next)) onChange(next);
          }}
          className="w-28 rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[12.5px] text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        />
        <span className="font-mono text-[10.5px] uppercase tracking-[0.12em] text-text-3">{unit}</span>
      </div>
      {invalid ? (
        <p id={errorId} role="alert" className="mt-1 text-[11px] text-warm-bright">
          {t("speech_rate_out_of_range", { max: SPEECH_RATE_MAX })}
        </p>
      ) : (
        <p className="mt-1 text-[11px] text-text-4">{t("speech_rate_hint")}</p>
      )}
    </div>
  );
}
