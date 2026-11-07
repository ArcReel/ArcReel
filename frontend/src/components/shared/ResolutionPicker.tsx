import { useId, useState } from "react";

import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export interface ResolutionPickerProps {
  mode: "select" | "combobox";
  options: string[];
  value: string | null;
  onChange: (v: string | null) => void;
  placeholder?: string;
  disabled?: boolean;
  "aria-label"?: string;
}

export function ResolutionPicker({
  mode,
  options,
  value,
  onChange,
  placeholder = "默认（不传）",
  disabled,
  "aria-label": ariaLabel,
}: ResolutionPickerProps) {
  const listId = useId();
  if (options.length === 0) return null;

  if (mode === "select") {
    // The null item is both placeholder copy and a Do not send option, allowing users to clear the selected tier from the overlay.
    const items = [{ value: null, label: placeholder }, ...options.map((o) => ({ value: o, label: o }))];
    return (
      <Select items={items} value={value} onValueChange={(next) => onChange(next)} disabled={disabled}>
        <SelectTrigger aria-label={ariaLabel} className="min-w-36">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {items.map((item) => (
            <SelectItem key={item.value ?? ""} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }

  return <ComboboxInput {...{ ariaLabel, listId, options, value, onChange, placeholder, disabled }} />;
}

interface ComboboxInputProps {
  ariaLabel?: string;
  listId: string;
  options: string[];
  value: string | null;
  onChange: (v: string | null) => void;
  placeholder: string;
  disabled?: boolean;
}

/** Custom-provider resolutions allow free entry with candidate suggestions, so use a datalist input rather than a dropdown. */
function ComboboxInput({ ariaLabel, listId, options, value, onChange, placeholder, disabled }: ComboboxInputProps) {
  // Local editing permits arbitrary input, including spaces and clearing; synchronize external value changes during render,
  // using React's recommended state-derived-from-props pattern rather than an effect.
  const [local, setLocal] = useState<string>(value ?? "");
  const [lastSync, setLastSync] = useState<string | null>(value);
  if (value !== lastSync) {
    setLastSync(value);
    setLocal(value ?? "");
  }

  return (
    <>
      <Input
        type="text"
        aria-label={ariaLabel}
        className="w-40"
        value={local}
        disabled={disabled}
        placeholder={placeholder}
        list={listId}
        onChange={(e) => {
          const raw = e.target.value;
          setLocal(raw);
          onChange(raw === "" ? null : raw);
        }}
        onBlur={() => {
          // Input may contain leading/trailing whitespace; normalize on blur to keep dirty values out of backend lookup tables
          const trimmed = local.trim();
          if (trimmed !== local) {
            setLocal(trimmed);
            onChange(trimmed === "" ? null : trimmed);
          }
        }}
      />
      <datalist id={listId}>
        {options.map((o) => (
          <option key={o} value={o} />
        ))}
      </datalist>
    </>
  );
}
