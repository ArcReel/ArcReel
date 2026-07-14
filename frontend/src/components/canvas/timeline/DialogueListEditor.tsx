import { useState } from "react";
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

/** 下一个稳定 key：取现有 key 数字后缀最大值 +1；空序列从 d0 起，与初始化命名对齐。
 *  纯函数，StrictMode 双调用幂等。 */
function nextKey(keys: string[]): string {
  if (keys.length === 0) return "d0";
  const max = keys.reduce((m, k) => Math.max(m, Number(k.slice(1)) || 0), 0);
  return `d${max + 1}`;
}

/** Editable list of speaker/line dialogue pairs. */
export function DialogueListEditor({
  dialogue,
  onChange,
}: DialogueListEditorProps) {
  const { t } = useTranslation("dashboard");

  // 数据模型无 id：在编辑态派生与条目一一绑定的稳定 key，增删时同步搬运，使受控输入
  // 节点按条目（而非按位置）复用，避免删除中间行后焦点跳行、编辑内容串到相邻行。
  const [keys, setKeys] = useState<string[]>(() => dialogue.map((_, i) => `d${i}`));

  // 外部整体替换（挂载后 adopt / revision 静默刷新）导致条目数与 key 数漂移时对齐：按位复用
  // 已有 key、尾部补新 key、裁掉多余。本地增删已同步搬运 key，长度恒等，不触发此分支。
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
