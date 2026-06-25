import { useTranslation } from "react-i18next";
import { Plus, X } from "lucide-react";
import type { Dialogue } from "@/types";
import { useAutoResizeTextarea } from "@/hooks/useAutoResizeTextarea";

interface DialogueListEditorProps {
  dialogue: Dialogue[];
  onChange: (dialogue: Dialogue[]) => void;
}

interface DialogueRowProps {
  value: Dialogue;
  onUpdate: (patch: Partial<Dialogue>) => void;
  onRemove: () => void;
}

/** A single speaker/line pair. The line uses an auto-growing textarea so long
 *  dialogue wraps and stays fully visible instead of being clipped. */
function DialogueRow({ value, onUpdate, onRemove }: DialogueRowProps) {
  const { t } = useTranslation("dashboard");
  const { ref, resize } = useAutoResizeTextarea(value.line);

  return (
    <div className="flex items-start gap-1.5">
      <input
        type="text"
        value={value.speaker}
        onChange={(e) => onUpdate({ speaker: e.target.value })}
        placeholder={t("speaker_placeholder")}
        className="dlg-input dlg-input--speaker w-16 shrink-0"
      />
      <textarea
        ref={ref}
        value={value.line}
        onChange={(e) => onUpdate({ line: e.target.value })}
        onKeyDown={(e) => {
          // A dialogue line stays single-line; the textarea only wraps long
          // text. Block Enter from inserting a newline, but let IME use it to
          // commit a candidate (isComposing).
          if (e.key === "Enter" && !e.nativeEvent.isComposing) {
            e.preventDefault();
          }
        }}
        onInput={resize}
        placeholder={t("line_placeholder")}
        rows={1}
        className="dlg-input min-w-0 flex-1 resize-none overflow-hidden"
      />
      <button
        type="button"
        onClick={onRemove}
        aria-label={t("dialogue_remove")}
        title={t("dialogue_remove")}
        className="focus-ring grid h-7 w-7 shrink-0 place-items-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.05)]"
        style={{ color: "var(--color-text-4)" }}
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

/** Editable list of speaker/line dialogue pairs. */
export function DialogueListEditor({
  dialogue,
  onChange,
}: DialogueListEditorProps) {
  const { t } = useTranslation("dashboard");

  const update = (index: number, patch: Partial<Dialogue>) => {
    const next = dialogue.map((d, i) =>
      i === index ? { ...d, ...patch } : d
    );
    onChange(next);
  };

  const remove = (index: number) => {
    onChange(dialogue.filter((_, i) => i !== index));
  };

  const add = () => {
    onChange([...dialogue, { speaker: "", line: "" }]);
  };

  return (
    <div className="flex flex-col gap-1.5">
      {dialogue.map((d, i) => (
        <DialogueRow
          key={i}
          value={d}
          onUpdate={(patch) => update(i, patch)}
          onRemove={() => remove(i)}
        />
      ))}

      <button
        type="button"
        onClick={add}
        className="focus-ring inline-flex items-center gap-1 self-start rounded-md px-2 py-1 text-[11.5px] transition-colors hover:bg-[oklch(1_0_0_/_0.05)]"
        style={{ color: "var(--color-text-3)" }}
      >
        <Plus className="h-3 w-3" />
        {t("add_dialogue")}
      </button>
    </div>
  );
}
