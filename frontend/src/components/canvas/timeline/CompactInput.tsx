import { useId } from "react";
import { Input } from "@/components/ui/input";

interface CompactInputProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  /** Read-only display: retain selectable text without accepting input. */
  readOnly?: boolean;
}

/** Single-line structured-prompt field: label left, input right, in the two columns of `PromptFieldGrid`. */
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
