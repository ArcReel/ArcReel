import { useId, type ReactNode } from "react";
import { Input } from "@/components/ui/input";

export interface OptionalNumberFieldProps {
  /** Translated field names. */
  label: string;
  /** null means empty and is valid. */
  value: number | null;
  onChange: (next: number | null) => void;
  /** Units to the right of inputs. */
  unit: ReactNode;
  /** Inline explanations for valid values. */
  hint: ReactNode;
  /** Inline out-of-range hints replacing hint. */
  errorMessage: string;
  /**
   * Whether values are out of range. Callers own predicates: fields have different hard ranges/integer requirements, and submission gating reads the same
   * predicate functions; recalculating inside wrappers would create two sources of truth.
   */
  invalid: boolean;
  /** Field names incorporated into input IDs so labels bind independently for multiple fields on a page. */
  idSuffix: string;
  inputMode: "numeric" | "decimal";
  max: number;
  step: number | "any";
}

/**
 * Optional numeric fields: numeric inputs + units + inline hints; empty strings clear values.
 *
 * Native constraints must not exceed caller predicates, otherwise values appear both custom-valid and browser-invalid:
 * `min` stays 0 (`invalid` checks true lower bounds); `max` / `step` express units only.
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
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-xs font-medium text-muted-foreground">
        {label}
      </label>
      <div className="flex items-center gap-2">
        <Input
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
            // Block nonfinite numbers only (NaN / Infinity serialize to null, mistakenly triggering Clear);
            // leave range validation to inline hints below/backends, without swallowing keystrokes during input.
            if (Number.isFinite(next)) onChange(next);
          }}
          className="w-28"
        />
        <span className="text-xs text-muted-foreground">{unit}</span>
      </div>
      {invalid ? (
        <p id={errorId} role="alert" className="text-xs text-destructive">
          {errorMessage}
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">{hint}</p>
      )}
    </div>
  );
}
