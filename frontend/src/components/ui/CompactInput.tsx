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
    <div className={`flex items-center gap-2 ${className ?? ""}`}>
      <span className="shrink-0 text-[11px] text-gray-500">{label}</span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="min-w-0 flex-1 rounded bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-200 placeholder-gray-500 focus:border-indigo-500 focus-ring"
      />
    </div>
  );
}
