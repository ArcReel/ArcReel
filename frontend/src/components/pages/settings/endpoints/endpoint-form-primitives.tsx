import { createContext, useCallback, useContext, useMemo, useRef } from "react";
import { Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { EndpointPathItem } from "@/types";
import { isPlainPath, pathItemText, type EndpointFormSection } from "./endpoint-definition-draft";
import { useRowIds } from "../useRowIds";

export const LABEL_CLS = "mb-1.5 block text-sm font-medium";
export const HINT_CLS = "mt-1.5 block text-xs text-muted-foreground";

// ---------------------------------------------------------------------------
// Variable insertion.
// ---------------------------------------------------------------------------
// Available variables insert tokens into most-recently focused inputs. Focus registers targets
// with their own onChange, preserving React controlled-value handling.

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
    // Remove targets when row deletion unmounts inputs; stale onChange would restore deleted fields.
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

/** Available-variable hint bars; clicks harmlessly do nothing without registered focus targets. */
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
            // Pointer down retains focus, inserting into the most recently focused inputs.
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
// Section containers.
// ---------------------------------------------------------------------------

/** Lifecycle sections: left vertical rails/step dots, numbered by actual submission-to-retrieval order. */
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
// Fields
// ---------------------------------------------------------------------------

interface TextFieldProps {
  /** Omitted labels leave fields visually unlabeled, with ariaLabel naming inline table fields accessibly. */
  label?: string;
  ariaLabel?: string;
  value: string;
  onChange?: (next: string) => void;
  readOnly?: boolean;
  /** Monospace for request paths/headers/variable names; also disable spellcheck. */
  mono?: boolean;
  /** Nonmonospace values still unsuitable for spellcheck, such as API addresses. */
  spellCheck?: boolean;
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
  spellCheck = mono ? false : undefined,
  hint,
  placeholder,
  insertable,
}: TextFieldProps) {
  const insertion = useInsertion();
  const bound = insertable && onChange && insertion ? insertion.bind(onChange) : {};
  return (
    <label className="block">
      {label && <span className={LABEL_CLS}>{label}</span>}
      <span className="block">
        <Input
          type="text"
          mono={mono}
          value={value}
          readOnly={readOnly}
          placeholder={placeholder}
          aria-label={label ? undefined : ariaLabel}
          autoComplete="off"
          spellCheck={spellCheck}
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

/** Form dropdowns use caller-localized options and disable entirely when readonly. */
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
 * Add rows keyed by names; only one unnamed row can exist, so disable buttons while preceding rows lack names,
 * explaining visibly because title alone is unavailable to keyboard/screen-reader users.
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
// Extraction-path editors
// ---------------------------------------------------------------------------

/**
 * Numbered path lists take first matching values in order. json_decode paths display readonly,
 * requiring JSON edits because forms cannot represent nested decoding without silent field loss.
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
  // Editable/repeated path text cannot key rows; middle deletion must not shift other inputs.
  const rowIds = useRowIds(paths.length);
  return (
    <div>
      <span className={LABEL_CLS}>{label}</span>
      <div className="flex flex-col gap-1.5">
        {paths.map((item, index) => (
          <div key={rowIds.ids[index]} className="flex items-center gap-2">
            <span aria-hidden className="w-4 shrink-0 text-right text-xs text-muted-foreground tabular-nums">
              {index + 1}
            </span>
            <span className="min-w-0 flex-1">
              <Input
                type="text"
                mono
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
                onClick={() => {
                  rowIds.remove(index);
                  onChange(paths.filter((_, i) => i !== index));
                }}
              />
            )}
          </div>
        ))}
      </div>
      {!readOnly && (
        <Button variant="ghost" size="xs" className="mt-1.5" onClick={() => {
            rowIds.append();
            onChange([...paths, ""]);
          }}>
          <Plus aria-hidden data-icon="inline-start" />
          {t("ce_path_add")}
        </Button>
      )}
      {hint && <span className={HINT_CLS}>{hint}</span>}
    </div>
  );
}
