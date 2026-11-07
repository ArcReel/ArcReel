import { Autocomplete } from "@base-ui/react/autocomplete";

import { InputGroup, InputGroupInput } from "@/components/ui/input-group";
import type { TtsVoiceOption } from "@/types";

interface Props {
  id: string;
  value: string;
  onChange: (next: string) => void;
  /** Voice list for the selected model; when empty, this is an ordinary input. */
  voices: readonly TtsVoiceOption[];
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
}

/** The voice list only supplies suggestions; cloned voices and system voices outside the list can still be entered manually. */
export function NarrationVoiceInput({ id, value, onChange, voices, ...aria }: Props) {
  return (
    <Autocomplete.Root items={voices} itemToStringValue={(voice) => voice.id} value={value} onValueChange={onChange} openOnInputClick>
      <InputGroup>
        <Autocomplete.Input id={id} autoComplete="off" spellCheck={false} render={<InputGroupInput />} {...aria} />
      </InputGroup>
      <Autocomplete.Portal>
        <Autocomplete.Positioner sideOffset={6} align="start" className="isolate z-overlay">
          <Autocomplete.Popup className="max-h-(--available-height) w-(--anchor-width) overflow-hidden rounded-lg bg-popover text-popover-foreground shadow-overlay ring-1 ring-foreground/10 data-closed:animate-out data-closed:fade-out-0 data-open:animate-in data-open:fade-in-0">
            <Autocomplete.List className="relative max-h-72 overflow-y-auto overscroll-contain p-1 data-empty:p-0">
              {(voice: TtsVoiceOption) => (
                <Autocomplete.Item
                  key={voice.id}
                  value={voice}
                  className="cursor-default rounded-md px-2 py-1 text-sm wrap-break-word outline-hidden select-none data-highlighted:bg-accent data-highlighted:text-accent-foreground"
                >
                  {voice.id} · {voice.label}
                </Autocomplete.Item>
              )}
            </Autocomplete.List>
          </Autocomplete.Popup>
        </Autocomplete.Positioner>
      </Autocomplete.Portal>
    </Autocomplete.Root>
  );
}
