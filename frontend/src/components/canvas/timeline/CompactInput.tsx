import { useId } from "react";
import { Input } from "@/components/ui/input";

interface CompactInputProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  /** 只读展示：保留可选中的文本，但不接受输入。 */
  readOnly?: boolean;
}

/** 结构化提示词里的单行字段：左侧标签、右侧输入框，放在 `PromptFieldGrid` 的两列里。 */
export function CompactInput({ label, value, onChange, placeholder, readOnly }: CompactInputProps) {
  const id = useId();
  return (
    <>
      <label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </label>
      <Input
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        readOnly={readOnly}
        placeholder={placeholder}
        className="min-w-0"
      />
    </>
  );
}
