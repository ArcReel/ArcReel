import { useMemo, useState } from "react";
import {
  Combobox,
  ComboboxButton,
  ComboboxInput,
  ComboboxOption,
  ComboboxOptions,
} from "@headlessui/react";
import { ChevronDown, X } from "lucide-react";

const inputClassName =
  "w-full rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[13px] text-text placeholder:text-text-4 transition-colors hover:border-hairline-strong focus:border-accent/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50";

const smallBtnClassName =
  "rounded-[5px] p-1 text-text-4 transition-colors hover:text-text-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";

export interface ModelComboboxProps {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  options: string[];
  placeholder?: string;
  name?: string;
  disabled?: boolean;
  "aria-label"?: string;
  /** 显示清除按钮（在 value 非空时）。aria-label 通过 clearAriaLabel 提供。 */
  clearable?: boolean;
  clearAriaLabel?: string;
}

export function ModelCombobox({
  id,
  value,
  onChange,
  options,
  placeholder,
  name,
  disabled,
  "aria-label": ariaLabel,
  clearable,
  clearAriaLabel,
}: ModelComboboxProps) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    if (query === "") return options;
    const q = query.toLowerCase();
    return options.filter((o) => o.toLowerCase().includes(q));
  }, [options, query]);

  const showClear = clearable && !!value;
  const rightPadding = showClear ? "pr-14" : "pr-8";

  return (
    <Combobox
      value={value}
      onChange={(v) => {
        onChange(v ?? "");
        setQuery("");
      }}
      immediate
      disabled={disabled}
    >
      <div className="relative">
        <ComboboxInput
          id={id}
          name={name}
          aria-label={ariaLabel}
          placeholder={placeholder}
          autoComplete="off"
          spellCheck={false}
          className={`${inputClassName} ${rightPadding}`}
          displayValue={(v: string | null) => v ?? ""}
          onChange={(e) => {
            const next = e.target.value;
            setQuery(next);
            onChange(next);
          }}
        />

        {showClear && (
          <button
            type="button"
            onClick={() => {
              setQuery("");
              onChange("");
            }}
            className={`absolute right-8 top-1/2 -translate-y-1/2 ${smallBtnClassName}`}
            aria-label={clearAriaLabel}
            disabled={disabled}
            tabIndex={-1}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}

        <ComboboxButton
          className={`absolute right-2 top-1/2 -translate-y-1/2 ${smallBtnClassName}`}
          aria-label="toggle"
        >
          <ChevronDown className="h-4 w-4" />
        </ComboboxButton>

        {filtered.length > 0 && (
          <ComboboxOptions
            anchor="bottom start"
            className="z-50 mt-1 w-[var(--input-width)] max-h-60 overflow-auto rounded-lg border border-gray-700 bg-gray-900 py-1 shadow-xl focus:outline-none"
          >
            {filtered.map((option) => (
              <ComboboxOption
                key={option}
                value={option}
                className="cursor-pointer select-none px-3 py-2 text-sm text-gray-200 data-[focus]:bg-gray-800 data-[focus]:text-white"
              >
                {option}
              </ComboboxOption>
            ))}
          </ComboboxOptions>
        )}
      </div>
    </Combobox>
  );
}
