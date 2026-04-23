import { useEffect, useId, useState } from "react";

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
    return (
      <select
        aria-label={ariaLabel}
        className="rounded-lg border border-gray-700 bg-gray-900 px-3 py-1.5 text-sm text-gray-100"
        value={value ?? ""}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
      >
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    );
  }

  // combobox：<input list=...>
  return (
    <ComboboxInput
      ariaLabel={ariaLabel}
      listId={listId}
      options={options}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      disabled={disabled}
    />
  );
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

function ComboboxInput({
  ariaLabel,
  listId,
  options,
  value,
  onChange,
  placeholder,
  disabled,
}: ComboboxInputProps) {
  // 本地编辑态：允许用户自由输入任何文本（包括清空），
  // 与外部 value prop 仅在外部变更时同步，避免受控 reconciliation 吞事件。
  const [local, setLocal] = useState<string>(value ?? "");
  useEffect(() => {
    setLocal(value ?? "");
  }, [value]);

  return (
    <>
      <input
        type="text"
        role="textbox"
        aria-label={ariaLabel}
        className="rounded-lg border border-gray-700 bg-gray-900 px-3 py-1.5 text-sm text-gray-100"
        value={local}
        disabled={disabled}
        placeholder={placeholder}
        list={listId}
        onChange={(e) => {
          const raw = e.target.value;
          setLocal(raw);
          const v = raw.trim();
          onChange(v === "" ? null : v);
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
