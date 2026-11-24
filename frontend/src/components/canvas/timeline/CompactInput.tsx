import { useId } from "react";
import { Input } from "@/components/ui/input";

interface CompactInputProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  readOnly?: boolean;
}

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
