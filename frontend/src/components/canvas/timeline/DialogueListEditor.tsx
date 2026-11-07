import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, X } from "lucide-react";
import type { Dialogue } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { TooltipIconButton } from "./TooltipIconButton";

interface DialogueListEditorProps {
  dialogue: Dialogue[];
  onChange: (dialogue: Dialogue[]) => void;
  /** Read-only onboarding demos display dialogue without add/remove/edit. */
  readOnly?: boolean;
}

interface DialogueRowProps {
  value: Dialogue;
  onUpdate: (patch: Partial<Dialogue>) => void;
  onRemove: () => void;
  readOnly?: boolean;
}

/** One speaker/dialogue entry; text wraps/expands so long dialogue stays fully visible. */
function DialogueRow({ value, onUpdate, onRemove, readOnly }: DialogueRowProps) {
  const { t } = useTranslation("dashboard");

  return (
    <div className="flex items-start gap-1.5">
      <Input
        value={value.speaker}
        onChange={(e) => onUpdate({ speaker: e.target.value })}
        readOnly={readOnly}
        placeholder={t("speaker_placeholder")}
        aria-label={t("speaker_placeholder")}
        className="w-20 shrink-0"
      />
      <Textarea
        value={value.line}
        onChange={(e) => onUpdate({ line: e.target.value })}
        onKeyDown={(e) => {
          // One dialogue paragraph per entry: Enter does not insert newlines, long text wraps; permit IME candidate confirmation.
          if (e.key === "Enter" && !e.nativeEvent.isComposing) {
            e.preventDefault();
          }
        }}
        readOnly={readOnly}
        placeholder={t("line_placeholder")}
        aria-label={t("line_placeholder")}
        rows={1}
        className="min-h-8 min-w-0 flex-1"
      />
      {readOnly ? null : (
        <TooltipIconButton label={t("dialogue_remove")} onClick={onRemove}>
          <X aria-hidden />
        </TooltipIconButton>
      )}
    </div>
  );
}

/** Next stable key: largest numeric suffix +1, empty starts d0 to match initialization.
 *  Pure, idempotent under StrictMode double calls. */
function nextKey(keys: string[]): string {
  if (keys.length === 0) return "d0";
  const max = keys.reduce((m, k) => Math.max(m, Number(k.slice(1)) || 0), 0);
  return `d${max + 1}`;
}

/** Editable list of speaker/line dialogue pairs. */
export function DialogueListEditor({
  dialogue,
  onChange,
  readOnly,
}: DialogueListEditorProps) {
  const { t } = useTranslation("dashboard");

  // Models lack IDs; derive stable entry-bound keys while editing and move them with add/remove, reusing controlled inputs
  // by entry rather than position, avoiding focus jumps/content leaks after middle-row deletion.
  const [keys, setKeys] = useState<string[]>(() => dialogue.map((_, i) => `d${i}`));

  // External whole replacement (postmount adopt/silent revision refresh) may mismatch entry/key counts; reuse by position,
  // append new keys, truncate extras. Local mutations already move keys and retain equal lengths, bypassing this branch.
  let renderKeys = keys;
  if (keys.length !== dialogue.length) {
    renderKeys = keys.slice(0, dialogue.length);
    while (renderKeys.length < dialogue.length) renderKeys.push(nextKey(renderKeys));
    setKeys(renderKeys);
  }

  const update = (index: number, patch: Partial<Dialogue>) => {
    const next = dialogue.map((d, i) =>
      i === index ? { ...d, ...patch } : d
    );
    onChange(next);
  };

  const remove = (index: number) => {
    onChange(dialogue.filter((_, i) => i !== index));
    setKeys((prev) => prev.filter((_, i) => i !== index));
  };

  const add = () => {
    onChange([...dialogue, { speaker: "", line: "" }]);
    setKeys((prev) => [...prev, nextKey(prev)]);
  };

  return (
    <div className="flex flex-col gap-1.5">
      {dialogue.map((d, i) => (
        <DialogueRow
          key={renderKeys[i]}
          value={d}
          onUpdate={(patch) => update(i, patch)}
          onRemove={() => remove(i)}
          readOnly={readOnly}
        />
      ))}

      {readOnly ? null : (
        <Button variant="ghost" size="xs" className="self-start" onClick={add}>
          <Plus aria-hidden data-icon="inline-start" />
          {t("add_dialogue")}
        </Button>
      )}
    </div>
  );
}
