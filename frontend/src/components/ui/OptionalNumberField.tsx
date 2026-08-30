import { useId, type ReactNode } from "react";
import { FieldLabel } from "@/components/ui/FieldLabel";

export interface OptionalNumberFieldProps {
  /** 已翻译的字段名。 */
  label: string;
  /** null = 未填，是合法取值。 */
  value: number | null;
  onChange: (next: number | null) => void;
  /** 输入框右侧的量纲。 */
  unit: ReactNode;
  /** 值合法时的行内说明。 */
  hint: ReactNode;
  /** 值越界时的行内提示，替代 hint 呈现。 */
  errorMessage: string;
  /**
   * 是否越界。判据留在调用方：各字段的硬区间与整数性要求不同，且提交前的 gating 读的是同一个
   * 判据函数，壳内另算一份会造出两套真相。
   */
  invalid: boolean;
  /** 拼进 input id 的字段名，令同页多个字段的 label 各自绑定。 */
  idSuffix: string;
  inputMode: "numeric" | "decimal";
  max: number;
  step: number | "any";
}

/**
 * 可选数值字段：一个数字输入 + 量纲 + 行内提示，空串即清空取值。
 *
 * 原生约束不比调用方的判据更严，否则同一个值会同时呈现自定义有效与浏览器无效两种状态：
 * `min` 固定取 0（真实下界由 `invalid` 判），`max` / `step` 只表达量纲。
 */
export function OptionalNumberField({
  label,
  value,
  onChange,
  unit,
  hint,
  errorMessage,
  invalid,
  idSuffix,
  inputMode,
  max,
  step,
}: OptionalNumberFieldProps) {
  const id = `${useId()}-${idSuffix}`;
  const errorId = `${id}-error`;

  return (
    <div>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="number"
          inputMode={inputMode}
          min={0}
          max={max}
          step={step}
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
          {errorMessage}
        </p>
      ) : (
        <p className="mt-1 text-[11px] text-text-4">{hint}</p>
      )}
    </div>
  );
}
