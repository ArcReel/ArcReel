import { useId, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";

/** 目标总时长的常用档位，与新建项目向导一致；其他正整数秒走自定义。 */
const AD_TARGET_DURATION_TIERS = [15, 30, 60, 90] as const;

const FIELD_STYLE: CSSProperties = {
  background: "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.6), oklch(0.18 0.010 265 / 0.45))",
  border: "1px solid var(--color-hairline)",
  color: "var(--color-text)",
  boxShadow: "inset 0 1px 2px oklch(0 0 0 / 0.2)",
};
const CHIP_BASE =
  "cursor-pointer rounded-[7px] border px-3 py-1.5 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent ";
const CHIP_ACTIVE = "border-accent/45 bg-accent-dim text-accent-2";
const CHIP_IDLE = "border-hairline-soft bg-bg-grad-a/55 text-text-3 hover:border-hairline hover:text-text";

function parseSeconds(text: string): number | null {
  if (!/^\d+$/.test(text.trim())) return null;
  const value = Number(text.trim());
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

interface Props {
  /** 当前目标总时长（秒）；自定义输入不是正整数时为 null。 */
  value: number | null;
  onChange: (value: number | null) => void;
  disabled?: boolean;
}

/**
 * 广告/短片的目标总时长：常用档位单选，其余正整数秒在「自定义」里填写。
 * 自定义输入不是正整数时回报 null，调用方据此拦住保存，行内提示说明原因。
 */
export function AdTargetDurationField({ value, onChange, disabled = false }: Props) {
  const { t } = useTranslation("dashboard");
  const groupName = useId();
  const isTier = AD_TARGET_DURATION_TIERS.some((tier) => tier === value);
  const [custom, setCustom] = useState(!isTier);
  const [customText, setCustomText] = useState(!isTier && value !== null ? String(value) : "");

  const chooseCustom = () => {
    setCustom(true);
    onChange(parseSeconds(customText));
  };

  return (
    <div>
      <p className="mb-1.5 text-[12px] font-medium" style={{ color: "var(--color-text-2)" }}>
        {t("target_duration_label")}
      </p>
      <div className="flex flex-wrap items-center gap-2" role="radiogroup" aria-label={t("target_duration_label")}>
        {AD_TARGET_DURATION_TIERS.map((tier) => {
          const active = !custom && value === tier;
          return (
            <label key={tier} className={CHIP_BASE + (active ? CHIP_ACTIVE : CHIP_IDLE)}>
              <input
                type="radio"
                name={groupName}
                checked={active}
                onChange={() => {
                  setCustom(false);
                  onChange(tier);
                }}
                disabled={disabled}
                className="sr-only"
              />
              {t("duration_seconds_value_text", { value: tier })}
            </label>
          );
        })}
        <label className={CHIP_BASE + (custom ? CHIP_ACTIVE : CHIP_IDLE)}>
          <input
            type="radio"
            name={groupName}
            checked={custom}
            onChange={chooseCustom}
            disabled={disabled}
            className="sr-only"
          />
          {t("ad_target_duration_custom")}
        </label>
        {custom && (
          <input
            type="number"
            inputMode="numeric"
            min={1}
            step={1}
            value={customText}
            onChange={(event) => {
              setCustomText(event.target.value);
              onChange(parseSeconds(event.target.value));
            }}
            disabled={disabled}
            aria-label={t("ad_target_duration_custom_label")}
            aria-invalid={value === null}
            className="focus-ring w-24 rounded-lg px-3 py-1.5 text-[13px] outline-none"
            style={FIELD_STYLE}
          />
        )}
      </div>
      {value === null && (
        <p role="alert" className="mt-1.5 text-[11.5px] text-red-300">
          {t("ad_target_duration_invalid")}
        </p>
      )}
    </div>
  );
}
