import { useId, type ReactNode } from "react";

interface FieldRowProps {
  label: ReactNode;
  htmlFor?: string;
  group?: boolean;
  hint?: ReactNode;
  children: ReactNode;
}

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

export function FieldHint({ children, tone }: { children: ReactNode; tone?: "warn" }) {
  return <p className={tone === "warn" ? "text-xs text-warn" : "text-xs text-muted-foreground"}>{children}</p>;
}
