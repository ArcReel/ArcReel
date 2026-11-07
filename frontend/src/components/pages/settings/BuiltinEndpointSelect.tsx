import { useTranslation } from "react-i18next";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { BuiltinEndpointOption } from "@/types";

interface BuiltinEndpointSelectProps {
  /** Trigger button ID for the field label's `htmlFor` association. */
  id?: string;
  value: string;
  onChange: (next: string) => void;
  /** Built-in endpoints for this provider and media type, supplied by the server. */
  options: BuiltinEndpointOption[];
}

/** Endpoint picker for user-added models under built-in providers: list only that provider's built-in endpoints for the media type. */
export function BuiltinEndpointSelect({ id, value, onChange, options }: BuiltinEndpointSelectProps) {
  const { t } = useTranslation("dashboard");
  const label = (option: BuiltinEndpointOption) =>
    option.display_name_key ? t(option.display_name_key) : (option.display_name ?? option.key);
  const selected = options.find((option) => option.key === value);

  return (
    <Select<string>
      value={value}
      onValueChange={(next) => {
        if (next !== null && next !== value) onChange(next);
      }}
      disabled={options.length < 2}
    >
      <SelectTrigger id={id} className="w-full">
        <SelectValue>
          {() => <span className="truncate">{selected ? label(selected) : value}</span>}
        </SelectValue>
      </SelectTrigger>
      <SelectContent alignItemWithTrigger={false}>
        {options.map((option) => (
          <SelectItem key={option.key} value={option.key}>
            <span className="truncate">{label(option)}</span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
