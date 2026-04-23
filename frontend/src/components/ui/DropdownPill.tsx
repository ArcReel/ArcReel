import { useRef, useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { Popover } from "@/components/ui/Popover";

// ---------------------------------------------------------------------------
// DropdownPill
// ---------------------------------------------------------------------------

interface DropdownPillProps<T extends string> {
  value: T;
  options: readonly T[];
  onChange: (value: T) => void;
  label?: string;
  className?: string;
  renderOption?: (value: T) => ReactNode;
}

export function DropdownPill<T extends string>({
  value,
  options,
  onChange,
  label,
  className,
  renderOption,
}: DropdownPillProps<T>) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const display = (v: T): ReactNode => (renderOption ? renderOption(v) : v);

  return (
    <div ref={containerRef} className={`relative inline-block ${className ?? ""}`}>
      {/* Trigger */}
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="inline-flex items-center gap-1 rounded-full bg-gray-800 px-2.5 py-0.5 text-xs text-gray-300 transition-colors hover:bg-gray-700"
      >
        {label && <span className="text-gray-500">{label}</span>}
        <span>{display(value)}</span>
        <ChevronDown className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {/* Options popover */}
      <Popover
        open={open}
        onClose={() => setOpen(false)}
        anchorRef={containerRef}
        align="start"
        sideOffset={4}
        width="min-w-[140px]"
        className="overflow-hidden rounded-lg border border-gray-700 py-1 shadow-xl"
      >
        {options.map((opt) => (
          <button
            key={opt}
            type="button"
            onClick={() => {
              onChange(opt);
              setOpen(false);
            }}
            className={`flex w-full items-center px-3 py-1.5 text-left text-xs transition-colors ${
              opt === value
                ? "bg-indigo-600/20 text-indigo-400"
                : "text-gray-300 hover:bg-gray-800"
            }`}
          >
            {display(opt)}
          </button>
        ))}
      </Popover>
    </div>
  );
}
