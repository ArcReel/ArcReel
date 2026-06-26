import { useAutoResizeTextarea } from "@/hooks/useAutoResizeTextarea";

interface AutoTextareaProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  id?: string;
  disabled?: boolean;
  "aria-label"?: string;
  "aria-labelledby"?: string;
}

/** Auto-resizing textarea that grows with its content. */
export function AutoTextarea({
  value,
  onChange,
  placeholder,
  className,
  id,
  disabled,
  "aria-label": ariaLabel,
  "aria-labelledby": ariaLabelledBy,
}: AutoTextareaProps) {
  const { ref, resize } = useAutoResizeTextarea(value);

  return (
    <textarea
      ref={ref}
      id={id}
      aria-label={ariaLabel}
      aria-labelledby={ariaLabelledBy}
      disabled={disabled}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onInput={resize}
      placeholder={placeholder}
      rows={2}
      className={`focus-ring w-full resize-none overflow-hidden rounded-lg px-2.5 py-2 font-mono text-xs outline-none disabled:cursor-not-allowed disabled:opacity-60 ${className ?? ""}`}
      style={{
        background:
          "linear-gradient(180deg, oklch(0.225 0.003 285 / 0.55), oklch(0.195 0.003 285 / 0.4))",
        border: "1px solid var(--color-hairline-soft)",
        color: "var(--color-text)",
        boxShadow: "inset 0 1px 0 oklch(1 0 0 / 0.03)",
      }}
    />
  );
}
