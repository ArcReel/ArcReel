import { useId, type ReactNode } from "react";

interface FieldRowProps {
  label: ReactNode;
  /** Single control associated with its label through `<label htmlFor>`. */
  htmlFor?: string;
  /** Multiple controls in one row, such as prices: make the row a label-named group, with each control carrying `aria-label`. */
  group?: boolean;
  hint?: ReactNode;
  children: ReactNode;
}

/** Custom-provider form property row: shared-width label column on the left, controls and explanations on the right. */
export function FieldRow({ label, htmlFor, group, hint, children }: FieldRowProps) {
  const labelId = useId();
  return (
    <div className="grid grid-cols-[7rem_minmax(0,1fr)] items-start gap-x-4">
      {htmlFor ? (
        <label htmlFor={htmlFor} className="pt-1.5 text-sm text-muted-foreground">
          {label}
        </label>
      ) : (
        <span id={labelId} className="pt-1.5 text-sm text-muted-foreground">
          {label}
        </span>
      )}
      <div
        role={group ? "group" : undefined}
        aria-labelledby={group ? labelId : undefined}
        className="flex min-w-0 flex-col gap-1.5"
      >
        {children}
        {hint}
      </div>
    </div>
  );
}

/** One explanatory line below controls; `tone="warn"` indicates issues requiring user edits, such as malformed input. */
export function FieldHint({ children, tone }: { children: ReactNode; tone?: "warn" }) {
  return <p className={tone === "warn" ? "text-xs text-warn" : "text-xs text-muted-foreground"}>{children}</p>;
}
