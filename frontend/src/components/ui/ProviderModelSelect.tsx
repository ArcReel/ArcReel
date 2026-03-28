import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { ChevronDown, Check } from "lucide-react";
import { ProviderIcon } from "@/components/ui/ProviderIcon";

interface ProviderModelSelectProps {
  value: string; // "gemini-aistudio/veo-3.1-generate-001"
  options: string[]; // ["gemini-aistudio/veo-3.1-generate-001", ...]
  providerNames: Record<string, string>; // {"gemini-aistudio": "Gemini AI Studio", ...}
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  /** If true, adds a default option that returns empty string */
  allowDefault?: boolean;
  /** Label for the default option (defaults to "跟随全局默认") */
  defaultLabel?: string;
  defaultHint?: string; // "当前: gemini-aistudio/veo-3.1-generate-001"
  /** Accessible label for the trigger button */
  "aria-label"?: string;
}

interface FlatOption {
  type: "default" | "option";
  fullValue: string;
  label: string;
}

function groupByProvider(options: string[]): Record<string, string[]> {
  const groups: Record<string, string[]> = {};
  for (const opt of options) {
    const slashIdx = opt.indexOf("/");
    if (slashIdx === -1) continue;
    const provider = opt.slice(0, slashIdx);
    const model = opt.slice(slashIdx + 1);
    if (!groups[provider]) groups[provider] = [];
    groups[provider].push(model);
  }
  return groups;
}

const LISTBOX_ID = "provider-model-listbox";

export function ProviderModelSelect({
  value,
  options,
  providerNames,
  onChange,
  placeholder = "选择模型…",
  className,
  allowDefault,
  defaultLabel,
  defaultHint,
  "aria-label": ariaLabel,
}: ProviderModelSelectProps) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<Map<number, HTMLButtonElement>>(new Map());

  const grouped = groupByProvider(options);

  // Build a flat list of selectable options for keyboard navigation
  const flatOptions = useMemo(() => {
    const list: FlatOption[] = [];
    if (allowDefault) {
      list.push({ type: "default", fullValue: "", label: defaultLabel ?? "跟随全局默认" });
    }
    for (const [providerId, models] of Object.entries(grouped)) {
      for (const model of models) {
        list.push({
          type: "option",
          fullValue: `${providerId}/${model}`,
          label: model,
        });
      }
    }
    return list;
  }, [options, allowDefault, defaultLabel]);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Reset active index when opened — point to current value or 0
  useEffect(() => {
    if (open) {
      const idx = flatOptions.findIndex((o) => o.fullValue === value);
      setActiveIndex(idx >= 0 ? idx : 0);
    }
  }, [open]);

  // Scroll active item into view
  useEffect(() => {
    if (open) {
      itemRefs.current.get(activeIndex)?.scrollIntoView?.({ block: "nearest" });
    }
  }, [activeIndex, open]);

  const selectOption = useCallback(
    (optValue: string) => {
      onChange(optValue);
      setOpen(false);
      triggerRef.current?.focus();
    },
    [onChange],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (!open) {
        if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setOpen(true);
          return;
        }
        return;
      }

      switch (e.key) {
        case "ArrowDown":
          e.preventDefault();
          setActiveIndex((prev) => (prev + 1) % flatOptions.length);
          break;
        case "ArrowUp":
          e.preventDefault();
          setActiveIndex((prev) => (prev - 1 + flatOptions.length) % flatOptions.length);
          break;
        case "Home":
          e.preventDefault();
          setActiveIndex(0);
          break;
        case "End":
          e.preventDefault();
          setActiveIndex(flatOptions.length - 1);
          break;
        case "Enter":
        case " ": {
          e.preventDefault();
          const opt = flatOptions[activeIndex];
          if (opt) selectOption(opt.fullValue);
          break;
        }
        case "Escape":
          e.preventDefault();
          setOpen(false);
          triggerRef.current?.focus();
          break;
      }
    },
    [open, flatOptions, activeIndex, selectOption],
  );

  const slashIdx = value ? value.indexOf("/") : -1;
  const currentProvider = slashIdx !== -1 ? value.slice(0, slashIdx) : "";
  const currentModel = slashIdx !== -1 ? value.slice(slashIdx + 1) : "";

  const displayText = value
    ? `${providerNames[currentProvider] || currentProvider} · ${currentModel}`
    : placeholder;

  const activeDescendantId =
    open && flatOptions.length > 0 ? `${LISTBOX_ID}-option-${activeIndex}` : undefined;

  // Track flat index across grouped rendering
  let flatIdx = allowDefault ? 1 : 0;

  return (
    <div ref={containerRef} className={`relative ${className || ""}`}>
      {/* Trigger button */}
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={LISTBOX_ID}
        aria-activedescendant={activeDescendantId}
        aria-label={ariaLabel}
        onClick={() => setOpen(!open)}
        onKeyDown={handleKeyDown}
        className="flex w-full items-center justify-between gap-2 rounded-lg border border-gray-700 bg-gray-900/80 px-3 py-2 text-sm text-gray-200 transition-colors hover:border-gray-600 hover:bg-gray-800/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-1 focus-visible:ring-offset-gray-900"
      >
        <span className="truncate">{displayText}</span>
        <ChevronDown
          className={`h-4 w-4 shrink-0 text-gray-400 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {/* Dropdown panel */}
      {open && (
        <div
          id={LISTBOX_ID}
          role="listbox"
          aria-label="选择模型"
          className="absolute z-50 mt-1 w-full max-h-60 overflow-y-auto rounded-lg border border-gray-700 bg-gray-900 shadow-xl"
        >
          {allowDefault && (
            <button
              ref={(el) => {
                if (el) itemRefs.current.set(0, el);
                else itemRefs.current.delete(0);
              }}
              id={`${LISTBOX_ID}-option-0`}
              role="option"
              aria-selected={value === ""}
              type="button"
              onClick={() => selectOption("")}
              onMouseEnter={() => setActiveIndex(0)}
              className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors ${
                activeIndex === 0 ? "bg-gray-800 text-white" : "text-gray-300 hover:bg-gray-800/50"
              }`}
            >
              <span>{defaultLabel ?? "跟随全局默认"}</span>
              {defaultHint && (
                <span className="ml-auto text-xs text-gray-500">{defaultHint}</span>
              )}
            </button>
          )}

          {Object.entries(grouped).map(([providerId, models]) => (
            <div key={providerId} role="presentation">
              {/* Group header */}
              <div
                role="presentation"
                className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-medium uppercase tracking-wider text-gray-500 bg-gray-950/50"
              >
                <ProviderIcon providerId={providerId} className="h-3.5 w-3.5" />
                {providerNames[providerId] || providerId}
              </div>
              {/* Model options */}
              {models.map((model) => {
                const currentFlatIdx = flatIdx++;
                const fullValue = `${providerId}/${model}`;
                const isSelected = fullValue === value;
                const isActive = currentFlatIdx === activeIndex;
                return (
                  <button
                    key={fullValue}
                    ref={(el) => {
                      if (el) itemRefs.current.set(currentFlatIdx, el);
                      else itemRefs.current.delete(currentFlatIdx);
                    }}
                    id={`${LISTBOX_ID}-option-${currentFlatIdx}`}
                    role="option"
                    aria-selected={isSelected}
                    type="button"
                    onClick={() => selectOption(fullValue)}
                    onMouseEnter={() => setActiveIndex(currentFlatIdx)}
                    className={`flex w-full items-center gap-1.5 px-3 py-2 pl-6 text-left text-sm transition-colors ${
                      isActive
                        ? "bg-gray-800 text-white"
                        : "text-gray-300 hover:bg-gray-800/50"
                    }`}
                  >
                    {isSelected ? (
                      <Check className="h-3.5 w-3.5 shrink-0" />
                    ) : (
                      <span className="h-3.5 w-3.5 shrink-0" />
                    )}
                    <span className="truncate">{model}</span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
