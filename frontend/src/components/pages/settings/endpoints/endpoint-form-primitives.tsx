import { createContext, useCallback, useContext, useMemo, useRef } from "react";
import { Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { CARD_STYLE, GHOST_BTN_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";
import type { EndpointPathItem } from "@/types";
import { isPlainPath, pathItemText, type EndpointFormSection } from "./endpoint-definition-draft";

export const LABEL_CLS = "mb-1 block text-[12px] font-medium text-text-2";
export const HINT_CLS = "mt-1.5 block text-[12px] leading-[1.55] text-text-3";
export const MONO_INPUT_CLS = "font-mono text-[12px]";

// ---------------------------------------------------------------------------
// 变量插入
// ---------------------------------------------------------------------------
// 「可用变量 · 点击插入」把 token 写进最近获得焦点的输入框。目标登记在 focus 时发生，
// 携带该框自己的 onChange，插入因此不必绕过 React 的受控值。

interface InsertionTarget {
  el: HTMLInputElement | HTMLTextAreaElement;
  onChange: (next: string) => void;
}

interface InsertionApi {
  bind: (onChange: (next: string) => void) => {
    onFocus: (e: React.FocusEvent<HTMLInputElement | HTMLTextAreaElement>) => void;
  };
  insert: (token: string) => void;
}

const InsertionContext = createContext<InsertionApi | null>(null);

export function VariableInsertionProvider({ children }: { children: React.ReactNode }) {
  const targetRef = useRef<InsertionTarget | null>(null);

  const bind = useCallback(
    (onChange: (next: string) => void) => ({
      onFocus: (e: React.FocusEvent<HTMLInputElement | HTMLTextAreaElement>) => {
        targetRef.current = { el: e.currentTarget, onChange };
      },
    }),
    [],
  );

  const insert = useCallback((token: string) => {
    const target = targetRef.current;
    if (!target) return;
    const { el, onChange } = target;
    const start = el.selectionStart ?? el.value.length;
    const end = el.selectionEnd ?? start;
    onChange(el.value.slice(0, start) + token + el.value.slice(end));
    const caret = start + token.length;
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(caret, caret);
    });
  }, []);

  const api = useMemo(() => ({ bind, insert }), [bind, insert]);
  return <InsertionContext.Provider value={api}>{children}</InsertionContext.Provider>;
}

function useInsertion(): InsertionApi | null {
  return useContext(InsertionContext);
}

/** 可用变量提示条。没有登记过焦点目标时点击无害地什么都不做。 */
export function VariableChips({
  variables,
  note,
}: {
  variables: { token: string; desc: string }[];
  note?: string;
}) {
  const { t } = useTranslation("dashboard");
  const insertion = useInsertion();
  return (
    <div className="mt-3 rounded-[8px] border border-hairline-soft bg-bg-grad-a/35 px-3 py-2.5">
      <span className="mb-1.5 block text-[11.5px] text-text-3">{t("ce_variables_hint")}</span>
      <div className="flex flex-wrap gap-1.5">
        {variables.map((v) => (
          <button
            key={v.token}
            type="button"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => insertion?.insert(v.token)}
            className="inline-flex items-baseline gap-1.5 rounded-[6px] border border-hairline bg-bg-grad-a/55 px-2 py-1 transition-colors hover:border-accent/40 hover:bg-accent-dim focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <span className="font-mono text-[11px] text-accent-2">{v.token}</span>
            <span className="text-[11px] text-text-3">{v.desc}</span>
          </button>
        ))}
      </div>
      {note && <span className="mt-1.5 block text-[11.5px] leading-[1.5] text-text-3">{note}</span>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 分节容器
// ---------------------------------------------------------------------------

/** 生命周期分节：左侧竖轨 + 步骤圆点，编号即任务从提交到取件的真实次序。 */
export function FormSection({
  id,
  step,
  title,
  desc,
  children,
}: {
  id: EndpointFormSection;
  step: number;
  title: string;
  desc: string;
  children: React.ReactNode;
}) {
  return (
    <section id={`ce-section-${id}`} aria-labelledby={`ce-section-${id}-title`} className="relative scroll-mt-4 pl-7">
      <span aria-hidden className="absolute bottom-0 left-[7px] top-6 w-px bg-hairline-soft" />
      <span
        aria-hidden
        className="absolute left-0 top-0.5 grid h-4 w-4 place-items-center rounded-full border border-accent/40 bg-accent-dim font-mono text-[8.5px] font-bold text-accent-2"
      >
        {step}
      </span>
      <h3 id={`ce-section-${id}-title`} className="text-[13.5px] font-semibold text-text">
        {title}
      </h3>
      <p className="mb-2.5 mt-0.5 text-[12px] leading-[1.55] text-text-3">{desc}</p>
      <div className="mb-6 rounded-[10px] border border-hairline p-4" style={CARD_STYLE}>
        {children}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// 字段
// ---------------------------------------------------------------------------

interface TextFieldProps {
  /** 省略时字段不带可见标签，由 ariaLabel 承担无障碍名（用于表格式的行内字段）。 */
  label?: string;
  ariaLabel?: string;
  value: string;
  onChange?: (next: string) => void;
  readOnly?: boolean;
  mono?: boolean;
  hint?: string;
  placeholder?: string;
  insertable?: boolean;
}

export function TextField({
  label,
  ariaLabel,
  value,
  onChange,
  readOnly,
  mono,
  hint,
  placeholder,
  insertable,
}: TextFieldProps) {
  const insertion = useInsertion();
  const bound = insertable && onChange && insertion ? insertion.bind(onChange) : {};
  return (
    <label className="block">
      {label && <span className={LABEL_CLS}>{label}</span>}
      <input
        type="text"
        value={value}
        readOnly={readOnly}
        placeholder={placeholder}
        aria-label={label ? undefined : ariaLabel}
        onChange={onChange ? (e) => onChange(e.target.value) : undefined}
        className={`${INPUT_CLS} ${mono ? MONO_INPUT_CLS : ""}`}
        {...bound}
      />
      {hint && <span className={HINT_CLS}>{hint}</span>}
    </label>
  );
}

export function CheckboxField({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex items-center gap-2 text-[12.5px] text-text-2">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="h-3.5 w-3.5 accent-[var(--color-accent)]"
      />
      {label}
    </label>
  );
}

export function RowDeleteButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className="grid h-8 w-8 place-items-center rounded-[6px] text-text-3 transition-colors hover:bg-bg-grad-a/55 hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      <Trash2 className="h-3.5 w-3.5" aria-hidden />
    </button>
  );
}

/**
 * 添加一行。行以名字为键，未命名的行只能存在一个，因此上一行未命名时按钮停用，
 * 并把原因写成可见文本——只挂 title 的话键盘与读屏用户读不到。
 */
export function AddRowButton({
  label,
  onClick,
  disabled,
  disabledHint,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  disabledHint?: string;
}) {
  return (
    <div className="mt-2">
      <button type="button" onClick={onClick} disabled={disabled} className={GHOST_BTN_CLS}>
        <Plus className="h-3.5 w-3.5" aria-hidden />
        {label}
      </button>
      {disabled && disabledHint && <span className={HINT_CLS}>{disabledHint}</span>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 取值路径编辑器
// ---------------------------------------------------------------------------

/**
 * 编号路径列表：按顺序取第一个命中值。带 json_decode 的路径项只读展示，
 * 改动它们要切到 JSON 视图——表单里放不下嵌套解码的形态，硬塞会静默丢字段。
 */
export function PathsEditor({
  label,
  paths,
  onChange,
  readOnly,
  hint,
}: {
  label: string;
  paths: EndpointPathItem[];
  onChange: (next: EndpointPathItem[]) => void;
  readOnly?: boolean;
  hint?: string;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <div>
      <span className={LABEL_CLS}>{label}</span>
      <div className="space-y-1.5">
        {paths.map((item, index) => (
          <div key={index} className="flex items-center gap-2">
            <span aria-hidden className="w-4 shrink-0 text-right font-mono text-[11px] text-text-3">
              {index + 1}
            </span>
            <input
              type="text"
              value={pathItemText(item)}
              readOnly={readOnly || !isPlainPath(item)}
              aria-label={`${label} ${index + 1}`}
              placeholder="$.data.task_id"
              onChange={(e) => {
                const next = [...paths];
                next[index] = e.target.value;
                onChange(next);
              }}
              className={`${INPUT_CLS} ${MONO_INPUT_CLS} text-good/90`}
            />
            {!isPlainPath(item) && (
              <span className="shrink-0 text-[11px] text-text-3">{t("ce_path_json_only")}</span>
            )}
            {!readOnly && (
              <RowDeleteButton
                label={t("ce_path_remove")}
                onClick={() => onChange(paths.filter((_, i) => i !== index))}
              />
            )}
          </div>
        ))}
      </div>
      {!readOnly && (
        <button
          type="button"
          onClick={() => onChange([...paths, ""])}
          className="mt-1.5 rounded-[6px] border border-dashed border-hairline px-2 py-1 text-[11.5px] text-text-3 transition-colors hover:border-hairline-strong hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          {t("ce_path_add")}
        </button>
      )}
      {hint && <span className={HINT_CLS}>{hint}</span>}
    </div>
  );
}
