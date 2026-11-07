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

/** Enum fields in structured prompts (shot size, camera movement): label left, dropdown right, in the two columns of `PromptFieldGrid`. */
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
