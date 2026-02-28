import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown } from "lucide-react";
import { useAnchoredPopover } from "@/hooks/useAnchoredPopover";

// ---------------------------------------------------------------------------
// DropdownPill
// ---------------------------------------------------------------------------

interface DropdownPillProps<T extends string> {
  value: T;
  options: readonly T[];
  onChange: (value: T) => void;
  label?: string;
  className?: string;
}

export function DropdownPill<T extends string>({
  value,
  options,
  onChange,
  label,
  className,
}: DropdownPillProps<T>) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const { panelRef, positionStyle } = useAnchoredPopover({
    open,
    anchorRef: containerRef,
    onClose: () => setOpen(false),
    align: "start",
    sideOffset: 4,
  });

  return (
    <div ref={containerRef} className={`relative inline-block ${className ?? ""}`}>
      {/* Trigger */}
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="inline-flex items-center gap-1 rounded-full bg-gray-800 px-2.5 py-0.5 text-xs text-gray-300 transition-colors hover:bg-gray-700"
      >
        {label && <span className="text-gray-500">{label}</span>}
        <span>{value}</span>
        <ChevronDown className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {/* Options popover */}
      {open && typeof document !== "undefined" && createPortal(
        <div
          ref={panelRef}
          className="fixed z-40 min-w-[140px] overflow-hidden rounded-lg border border-gray-700 py-1 shadow-xl"
          style={{
            ...positionStyle,
            backgroundColor: "rgb(17 24 39)",
          }}
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
              {opt}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </div>
  );
}
