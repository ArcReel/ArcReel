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
    // null 项既是占位文案，也是列表里的「不传」选项，用户可以从弹层里清除已选档位。
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

/** 自定义供应商的分辨率可以自由填写，候选只作提示，所以用带 datalist 的输入框而不是下拉。 */
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
