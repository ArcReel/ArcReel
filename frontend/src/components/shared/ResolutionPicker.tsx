import { useId, useState } from "react";
import { INPUT_CLS } from "@/components/ui/darkroom-tokens";

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
        className={INPUT_CLS}
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

function ComboboxInput({ ariaLabel, listId, options, value, onChange, placeholder, disabled }: ComboboxInputProps) {
  // 本地编辑态允许用户自由输入（含空格/清空）——外部 value 变化时通过 render-phase
  // 判断同步（React 官方推荐的"派生 state from props"模式，非 effect）。
  const [local, setLocal] = useState<string>(value ?? "");
  const [lastSync, setLastSync] = useState<string | null>(value);
  if (value !== lastSync) {
    setLastSync(value);
    setLocal(value ?? "");
  }

  return (
    <>
      <input
        type="text"
        aria-label={ariaLabel}
        className={INPUT_CLS}
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
          // 输入后可能带首尾空格，离焦时 normalize 避免脏值流入后端查找表
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
