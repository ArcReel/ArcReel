import { Autocomplete } from "@base-ui/react/autocomplete";
import { ChevronDown, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";

interface ModelIdFieldProps {
  id: string;
  value: string;
  onChange: (value: string) => void;
  /** Candidate model IDs; filter by substring while typing. */
  options: string[];
  placeholder?: string;
  disabled?: boolean;
  "aria-describedby"?: string;
}

/**
 * Model ID input: select a candidate or enter any other ID, since gateway model lists are often incomplete.
 * Use Base UI Autocomplete rather than Combobox, which accepts candidates only.
 */
export function ModelIdField({ id, value, onChange, options, placeholder, disabled, ...rest }: ModelIdFieldProps) {
  const { t } = useTranslation("dashboard");
  return (
    <Autocomplete.Root items={options} value={value} onValueChange={onChange} openOnInputClick disabled={disabled}>
      <InputGroup>
        <Autocomplete.Input
          id={id}
          placeholder={placeholder}
          aria-describedby={rest["aria-describedby"]}
          autoComplete="off"
          spellCheck={false}
          render={<InputGroupInput mono />}
        />
        <InputGroupAddon align="inline-end">
          {value && (
            <Autocomplete.Clear render={<InputGroupButton size="icon-xs" aria-label={t("clear_input")} />}>
              <X aria-hidden />
            </Autocomplete.Clear>
          )}
          {options.length > 0 && (
            <Autocomplete.Trigger render={<InputGroupButton size="icon-xs" aria-label={t("model_options_toggle")} />}>
              <ChevronDown aria-hidden />
            </Autocomplete.Trigger>
          )}
        </InputGroupAddon>
      </InputGroup>
      <Autocomplete.Portal>
        <Autocomplete.Positioner sideOffset={6} align="start" className="isolate z-overlay">
          <Autocomplete.Popup className="max-h-(--available-height) w-(--anchor-width) overflow-hidden rounded-lg bg-popover text-popover-foreground shadow-overlay ring-1 ring-foreground/10 data-closed:animate-out data-closed:fade-out-0 data-open:animate-in data-open:fade-in-0">
            <Autocomplete.List className="relative max-h-72 overflow-y-auto overscroll-contain p-1 data-empty:p-0">
              {(item: string) => (
                <Autocomplete.Item
                  key={item}
                  value={item}
                  className="flex cursor-default items-center rounded-md px-2 py-1 font-mono text-sm outline-hidden select-none data-highlighted:bg-accent data-highlighted:text-accent-foreground"
                >
                  {item}
                </Autocomplete.Item>
              )}
            </Autocomplete.List>
          </Autocomplete.Popup>
        </Autocomplete.Positioner>
      </Autocomplete.Portal>
    </Autocomplete.Root>
  );
}
