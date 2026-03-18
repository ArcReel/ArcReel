import { useState, useRef, useEffect } from "react";
import { ChevronDown, Check } from "lucide-react";

interface ProviderModelSelectProps {
  value: string; // "gemini-aistudio/veo-3.1-generate-001"
  options: string[]; // ["gemini-aistudio/veo-3.1-generate-001", ...]
  providerNames: Record<string, string>; // {"gemini-aistudio": "Gemini AI Studio", ...}
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  /** If true, adds "跟随全局默认" option that returns empty string */
  allowDefault?: boolean;
  defaultHint?: string; // "当前: gemini-aistudio/veo-3.1-generate-001"
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

export function ProviderModelSelect({
  value,
  options,
  providerNames,
  onChange,
  placeholder = "选择模型...",
  className,
  allowDefault,
  defaultHint,
}: ProviderModelSelectProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const grouped = groupByProvider(options);

  const slashIdx = value ? value.indexOf("/") : -1;
  const currentProvider = slashIdx !== -1 ? value.slice(0, slashIdx) : "";
  const currentModel = slashIdx !== -1 ? value.slice(slashIdx + 1) : "";

  const displayText = value
    ? `${providerNames[currentProvider] || currentProvider} · ${currentModel}`
    : placeholder;

  return (
    <div ref={ref} className={`relative ${className || ""}`}>
      {/* Trigger button */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between gap-2 rounded-lg border border-gray-700 bg-gray-900/80 px-3 py-2 text-sm text-gray-200 transition-colors hover:border-gray-600 hover:bg-gray-800/80 focus:outline-none"
      >
        <span className="truncate">{displayText}</span>
        <ChevronDown
          className={`h-4 w-4 shrink-0 text-gray-400 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {/* Dropdown panel */}
      {open && (
        <div className="absolute z-50 mt-1 w-full overflow-hidden rounded-lg border border-gray-700 bg-gray-900 shadow-xl">
          {allowDefault && (
            <button
              type="button"
              onClick={() => {
                onChange("");
                setOpen(false);
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-300 hover:bg-gray-800/50"
            >
              <span>跟随全局默认</span>
              {defaultHint && (
                <span className="ml-auto text-xs text-gray-500">{defaultHint}</span>
              )}
            </button>
          )}

          {Object.entries(grouped).map(([providerId, models]) => (
            <div key={providerId}>
              {/* Group header */}
              <div className="px-3 py-1.5 text-[11px] font-medium uppercase tracking-wider text-gray-500 bg-gray-950/50">
                {providerNames[providerId] || providerId}
              </div>
              {/* Model options */}
              {models.map((model) => {
                const fullValue = `${providerId}/${model}`;
                const isSelected = fullValue === value;
                return (
                  <button
                    key={fullValue}
                    type="button"
                    onClick={() => {
                      onChange(fullValue);
                      setOpen(false);
                    }}
                    className={`flex w-full items-center gap-1.5 px-3 py-2 pl-6 text-left text-sm transition-colors ${
                      isSelected
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
