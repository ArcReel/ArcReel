import { useId } from "react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

interface PromptFieldSelectProps<T extends string> {
  label: string;
  value: T;
  options: readonly T[];
  renderOption: (value: T) => string;
  onChange: (value: T) => void;
  disabled?: boolean;
}

/** 结构化提示词里的枚举字段（景别、镜头运动）：左侧标签、右侧下拉，放在 `PromptFieldGrid` 的两列里。 */
export function PromptFieldSelect<T extends string>({
  label,
  value,
  options,
  renderOption,
  onChange,
  disabled,
}: PromptFieldSelectProps<T>) {
  const labelId = useId();
  return (
    <>
      <span id={labelId} className="text-xs text-muted-foreground">
        {label}
      </span>
      <Select
        value={value}
        disabled={disabled}
        onValueChange={(next: T | null) => {
          if (next !== null) onChange(next);
        }}
      >
        <SelectTrigger size="sm" aria-labelledby={labelId} className="min-w-0 justify-self-start">
          <SelectValue>{(selected: T) => renderOption(selected)}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option} value={option}>
              {renderOption(option)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </>
  );
}
