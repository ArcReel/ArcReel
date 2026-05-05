interface CompactInputProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}

/** Single-line labeled input with dark theme styling. */
export function CompactInput({
  label,
  value,
  onChange,
  placeholder,
  className,
}: CompactInputProps) {
  return (
    <label className={`flex items-center gap-2 ${className ?? ""}`}>
      <span
        className="shrink-0 text-[11px]"
        style={{ color: "var(--color-text-4)" }}
      >
        {label}
      </span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="focus-ring min-w-0 flex-1 rounded-md px-2 py-1 text-xs outline-none"
        style={{
          background:
            "linear-gradient(180deg, oklch(0.225 0.003 285 / 0.55), oklch(0.195 0.003 285 / 0.4))",
          border: "1px solid var(--color-hairline-soft)",
          color: "var(--color-text)",
          boxShadow: "inset 0 1px 0 oklch(1 0 0 / 0.03)",
        }}
      />
    </label>
  );
}
