import type { ReactNode } from "react";

interface FieldLabelProps {
  htmlFor?: string;
  required?: boolean;
  trailing?: ReactNode;
  className?: string;
  children: ReactNode;
}

const LABEL_CLS =
  "font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-2";

export function FieldLabel({
  htmlFor,
  required,
  trailing,
  className,
  children,
}: FieldLabelProps) {
  const wrapperClass = className ?? "mb-1.5";
  const inner = (
    <>
      {children}
      {required ? (
        <span aria-label="required" className="ml-1 text-warm-bright">
          *
        </span>
      ) : null}
    </>
  );
  if (trailing) {
    return (
      <div className={`flex items-center justify-between ${wrapperClass}`}>
        <label htmlFor={htmlFor} className={LABEL_CLS}>
          {inner}
        </label>
        {trailing}
      </div>
    );
  }
  return (
    <label htmlFor={htmlFor} className={`block ${LABEL_CLS} ${wrapperClass}`}>
      {inner}
    </label>
  );
}
