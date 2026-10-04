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
  /** 只读展示（引导演示项目）：对白可读，不能增删改。 */
  readOnly?: boolean;
}

interface DialogueRowProps {
  value: Dialogue;
  onUpdate: (patch: Partial<Dialogue>) => void;
  onRemove: () => void;
  readOnly?: boolean;
}

/** 一条「说话人 + 台词」。台词随内容撑高折行，长台词完整可见。 */
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
          // 一条台词只占一段：回车不换行，只折行显示长文本；输入法用回车确认候选时放行。
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
  readOnly,
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
