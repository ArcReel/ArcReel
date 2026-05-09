import { useMemo, useState } from "react";
import {
  Combobox,
  ComboboxButton,
  ComboboxInput,
  ComboboxOption,
  ComboboxOptions,
} from "@headlessui/react";
import { ChevronDown, X } from "lucide-react";
import { DROPDOWN_PANEL_STYLE, ICON_BTN_CLS, INPUT_CLS } from "./darkroom-tokens";

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
          className={`${INPUT_CLS} ${rightPadding}`}
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
            className={`absolute right-8 top-1/2 -translate-y-1/2 ${ICON_BTN_CLS}`}
            aria-label={clearAriaLabel}
            disabled={disabled}
            tabIndex={-1}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}

        <ComboboxButton
          className={`absolute right-2 top-1/2 -translate-y-1/2 ${ICON_BTN_CLS}`}
          aria-label="toggle"
        >
          <ChevronDown className="h-4 w-4" />
        </ComboboxButton>

        {filtered.length > 0 && (
          <ComboboxOptions
            anchor="bottom start"
            className="z-50 mt-1 w-[var(--input-width)] max-h-60 overflow-auto rounded-[8px] border border-hairline py-1 shadow-xl backdrop-blur focus:outline-none"
            style={DROPDOWN_PANEL_STYLE}
          >
            {filtered.map((option) => (
              <ComboboxOption
                key={option}
                value={option}
                className="cursor-pointer select-none px-3 py-2 text-[12.5px] text-text-2 data-[focus]:bg-accent-dim data-[focus]:text-text"
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
