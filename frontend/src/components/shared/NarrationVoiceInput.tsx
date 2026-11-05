import { useId } from "react";

import { Input } from "@/components/ui/input";
import type { TtsVoiceOption } from "@/types";

interface Props {
  id: string;
  value: string;
  onChange: (next: string) => void;
  /** 所选模型的音色表；为空时只是普通输入框。 */
  voices: readonly TtsVoiceOption[];
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
}

/** 旁白音色：音色表只作候选，克隆音色与表外系统音色照常手填，所以用带 datalist 的输入框而不是下拉。 */
export function NarrationVoiceInput({ id, value, onChange, voices, ...aria }: Props) {
  const listId = `${useId()}-voices`;
  return (
    <>
      <Input
        id={id}
        type="text"
        value={value}
        list={voices.length > 0 ? listId : undefined}
        onChange={(e) => onChange(e.target.value)}
        {...aria}
      />
      {voices.length > 0 && (
        <datalist id={listId}>
          {voices.map((voice) => (
            <option key={voice.id} value={voice.id}>
              {voice.label}
            </option>
          ))}
        </datalist>
      )}
    </>
  );
}
