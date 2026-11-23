import { useId, type ReactNode } from "react";
import { Input } from "@/components/ui/input";

export interface OptionalNumberFieldProps {
  label: string;
  value: number | null;
  onChange: (next: number | null) => void;
  unit: ReactNode;
  hint: ReactNode;
  errorMessage: string;
  invalid: boolean;
  idSuffix: string;
  inputMode: "numeric" | "decimal";
  max: number;
  step: number | "any";
}

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
