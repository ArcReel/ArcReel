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
        className="focus-ring inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs transition-colors"
        style={{
          background: "oklch(0.225 0.003 285 / 0.55)",
          border: "1px solid var(--color-hairline-soft)",
          color: "var(--color-text-2)",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "oklch(0.26 0.004 285 / 0.7)";
          e.currentTarget.style.color = "var(--color-text)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "oklch(0.225 0.003 285 / 0.55)";
          e.currentTarget.style.color = "var(--color-text-2)";
        }}
      >
        {label && <span style={{ color: "var(--color-text-4)" }}>{label}</span>}
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
        className="overflow-hidden rounded-lg py-1 shadow-xl"
        style={{
          background:
            "linear-gradient(180deg, oklch(0.21 0.005 285 / 0.96), oklch(0.18 0.004 285 / 0.96))",
          border: "1px solid var(--color-hairline)",
          backdropFilter: "blur(12px)",
          WebkitBackdropFilter: "blur(12px)",
        }}
      >
        {options.map((opt) => {
          const isActive = opt === value;
          return (
            <button
              key={opt}
              type="button"
              onClick={() => {
                onChange(opt);
                setOpen(false);
              }}
              className="flex w-full items-center px-3 py-1.5 text-left text-xs transition-colors"
              style={{
                background: isActive ? "var(--color-accent-dim)" : "transparent",
                color: isActive ? "var(--color-accent-2)" : "var(--color-text-2)",
              }}
              onMouseEnter={(e) => {
                if (!isActive) {
                  e.currentTarget.style.background = "oklch(1 0 0 / 0.04)";
                  e.currentTarget.style.color = "var(--color-text)";
                }
              }}
              onMouseLeave={(e) => {
                if (!isActive) {
                  e.currentTarget.style.background = "transparent";
                  e.currentTarget.style.color = "var(--color-text-2)";
                }
              }}
            >
              {display(opt)}
            </button>
          );
        })}
      </Popover>
    </div>
  );
}
