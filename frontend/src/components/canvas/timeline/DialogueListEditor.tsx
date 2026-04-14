import { useTranslation } from "react-i18next";
import { Plus, X } from "lucide-react";
import type { Dialogue } from "@/types";

interface DialogueListEditorProps {
  dialogue: Dialogue[];
  onChange: (dialogue: Dialogue[]) => void;
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
      <span className="text-[11px] text-gray-500">{t("dialogue_label")}</span>

      {dialogue.map((d, i) => (
        <div key={i} className="flex items-start gap-1.5">
          <input
            type="text"
            value={d.speaker}
            onChange={(e) => update(i, { speaker: e.target.value })}
            placeholder={t("speaker_placeholder")}
            className="w-16 shrink-0 rounded bg-gray-800 border border-gray-700 px-1.5 py-1 text-xs text-indigo-400 placeholder-gray-600 focus:border-indigo-500 focus-ring"
          />
          <input
            type="text"
            value={d.line}
            onChange={(e) => update(i, { line: e.target.value })}
            placeholder={t("line_placeholder")}
            className="min-w-0 flex-1 rounded bg-gray-800 border border-gray-700 px-1.5 py-1 text-xs text-gray-200 placeholder-gray-600 focus:border-indigo-500 focus-ring"
          />
          <button
            type="button"
            onClick={() => remove(i)}
            className="shrink-0 rounded p-0.5 text-gray-600 hover:bg-gray-800 hover:text-red-400"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}

      <button
        type="button"
        onClick={add}
        className="inline-flex items-center gap-1 self-start rounded px-2 py-0.5 text-xs text-gray-500 hover:bg-gray-800 hover:text-gray-300"
      >
        <Plus className="h-3 w-3" />
        {t("add_dialogue")}
      </button>
    </div>
  );
}
