import { createContext, useCallback, useContext, useMemo, useRef } from "react";
import { Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { EndpointPathItem } from "@/types";
import { isPlainPath, pathItemText, type EndpointFormSection } from "./endpoint-definition-draft";

export const LABEL_CLS = "mb-1.5 block text-sm font-medium";
export const HINT_CLS = "mt-1.5 block text-xs text-muted-foreground";

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
    // 目标输入框已随行删除卸载时撤销目标：残留的旧 onChange 会把值写回已删除的字段。
    if (!target.el.isConnected) {
      targetRef.current = null;
      return;
    }
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
    <div className="mt-3 flex flex-col gap-2 rounded-lg border border-border px-3 py-2.5">
      <span className="text-xs text-muted-foreground">{t("ce_variables_hint")}</span>
      <div className="flex flex-wrap gap-1.5">
        {variables.map((v) => (
          <Button
            key={v.token}
            variant="outline"
            size="xs"
            // 按下时不夺走焦点，插入点仍是最近聚焦的输入框
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => insertion?.insert(v.token)}
          >
            <span className="font-mono text-primary" translate="no">
              {v.token}
            </span>
            <span className="text-muted-foreground">{v.desc}</span>
          </Button>
        ))}
      </div>
      {note && <span className="text-xs text-muted-foreground">{note}</span>}
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
    <section id={`ce-section-${id}`} aria-labelledby={`ce-section-${id}-title`} className="relative scroll-mt-4 pl-8">
      <span aria-hidden className="absolute top-7 bottom-0 left-2.5 w-px bg-border" />
      <span
        aria-hidden
        className="absolute top-0.5 left-0 grid size-5 place-items-center rounded-full border border-primary/40 bg-primary/15 text-xs text-primary tabular-nums"
      >
        {step}
      </span>
      <h3 id={`ce-section-${id}-title`} className="text-base font-medium">
        {title}
      </h3>
      <p className="mt-0.5 mb-3 max-w-[40em] text-sm text-muted-foreground">{desc}</p>
      <div className="mb-6 rounded-lg border border-border p-4">{children}</div>
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
      {/* 等宽字体经继承进入输入框：Input 自己不接受字体 class */}
      <span className={mono ? "block font-mono" : "block"}>
        <Input
          type="text"
          value={value}
          readOnly={readOnly}
          placeholder={placeholder}
          aria-label={label ? undefined : ariaLabel}
          autoComplete="off"
          spellCheck={mono ? false : undefined}
          onChange={onChange ? (e) => onChange(e.target.value) : undefined}
          {...bound}
        />
      </span>
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
    <label className="flex items-center gap-2 text-sm text-subtle-foreground">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="size-4 accent-primary"
      />
      {label}
    </label>
  );
}

export function RowDeleteButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <Button variant="ghost" size="icon" onClick={onClick} aria-label={label}>
      <Trash2 aria-hidden />
    </Button>
  );
}

/** 表单内的下拉：选项文案由调用方本地化；只读时整体禁用。 */
export function SelectField<V extends string>({
  value,
  options,
  onChange,
  disabled,
  ariaLabel,
  className,
}: {
  value: V;
  options: readonly { value: V; label: string }[];
  onChange: (next: V) => void;
  disabled?: boolean;
  ariaLabel?: string;
  className?: string;
}) {
  return (
    <Select
      items={options}
      value={value}
      disabled={disabled}
      onValueChange={(next) => {
        if (next !== null) onChange(next);
      }}
    >
      <SelectTrigger aria-label={ariaLabel} className={cn("w-full min-w-0", className)}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
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
      <Button variant="outline" size="sm" onClick={onClick} disabled={disabled}>
        <Plus aria-hidden data-icon="inline-start" />
        {label}
      </Button>
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
      <div className="flex flex-col gap-1.5">
        {paths.map((item, index) => (
          <div key={index} className="flex items-center gap-2">
            <span aria-hidden className="w-4 shrink-0 text-right text-xs text-muted-foreground tabular-nums">
              {index + 1}
            </span>
            <span className="min-w-0 flex-1 font-mono">
              <Input
                type="text"
                value={pathItemText(item)}
                readOnly={readOnly || !isPlainPath(item)}
                aria-label={`${label} ${index + 1}`}
                placeholder="$.data.task_id"
                autoComplete="off"
                spellCheck={false}
                onChange={(e) => {
                  const next = [...paths];
                  next[index] = e.target.value;
                  onChange(next);
                }}
              />
            </span>
            {!isPlainPath(item) && (
              <span className="shrink-0 text-xs text-muted-foreground">{t("ce_path_json_only")}</span>
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
        <Button variant="ghost" size="xs" className="mt-1.5" onClick={() => onChange([...paths, ""])}>
          <Plus aria-hidden data-icon="inline-start" />
          {t("ce_path_add")}
        </Button>
      )}
      {hint && <span className={HINT_CLS}>{hint}</span>}
    </div>
  );
}
