import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ChevronDown,
  ChevronUp,
  MessageSquareQuote,
  Plus,
  Radio,
  X,
} from "lucide-react";
import { cn } from "cn";
import type { Utterance, UtteranceKind } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { TooltipIconButton } from "./TooltipIconButton";

interface UtteranceListEditorProps {
  utterances: Utterance[];
  onChange: (next: Utterance[]) => void;
  disabled?: boolean;
  /** 说话人输入框的候选名字；输入框仍可以写候选之外的名字（群演，不绑定声音）。 */
  speakerCandidates?: readonly string[];
}

function makeUtterance(kind: UtteranceKind): Utterance {
  return kind === "dialogue"
    ? { kind: "dialogue", speaker: "", text: "" }
    : { kind: "voiceover", speaker: null, text: "" };
}

/** 下一个稳定 key：取现有 key 数字后缀最大值 +1；空序列从 u0 起，与初始化命名对齐。
 *  纯函数，StrictMode 双调用幂等。 */
function nextKey(keys: string[]): string {
  if (keys.length === 0) return "u0";
  const max = keys.reduce((m, k) => Math.max(m, Number(k.slice(1)) || 0), 0);
  return `u${max + 1}`;
}

/** Flip an utterance's kind, preserving text. dialogue→voiceover drops the
 *  speaker; voiceover→dialogue opens an empty speaker for the author to fill. */
function flipKind(u: Utterance): Utterance {
  return u.kind === "dialogue"
    ? { kind: "voiceover", speaker: null, text: u.text }
    : { kind: "dialogue", speaker: "", text: u.text };
}

interface UtteranceRowProps {
  value: Utterance;
  index: number;
  total: number;
  disabled: boolean;
  /** 说话人候选的 datalist id；没有候选时为 undefined。 */
  speakerListId?: string;
  onUpdate: (next: Utterance) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
}

/**
 * 一条有序发声条目：左侧时序节点（台词实心、画外音空心）、类型切换、说话人（仅台词）、
 * 随内容撑高的文本与上移 / 下移 / 删除。台词缺说话人时标为无效（后端 kind ⇄ speaker 约束，保存时会拒）。
 */
function UtteranceRow({
  value,
  index,
  total,
  disabled,
  speakerListId,
  onUpdate,
  onMove,
  onRemove,
}: UtteranceRowProps) {
  const { t } = useTranslation("dashboard");
  const isDialogue = value.kind === "dialogue";
  const speaker = isDialogue ? value.speaker : "";
  const speakerMissing = isDialogue && !speaker.trim();

  return (
    <div className="relative flex gap-2.5">
      {/* 时序线 + 节点：实心 = 台词，空心 = 画外音 */}
      <div className="relative flex w-4 shrink-0 flex-col items-center">
        {index > 0 && <span aria-hidden="true" className="absolute top-0 h-2.5 w-px bg-border" />}
        <span
          aria-hidden="true"
          className={cn(
            "mt-2 size-2.5 rounded-full",
            isDialogue ? "bg-primary" : "border border-muted-foreground",
          )}
        />
        {index < total - 1 && <span aria-hidden="true" className="w-px flex-1 bg-border" />}
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-1 pb-2.5">
        <div className="flex items-center gap-1.5">
          <Tooltip>
            <TooltipTrigger
              render={
                <Button variant="outline" size="xs" disabled={disabled} onClick={() => onUpdate(flipKind(value))} />
              }
            >
              {isDialogue ? (
                <MessageSquareQuote aria-hidden data-icon="inline-start" />
              ) : (
                <Radio aria-hidden data-icon="inline-start" />
              )}
              {isDialogue ? t("utterance_kind_dialogue") : t("utterance_kind_voiceover")}
            </TooltipTrigger>
            <TooltipContent>{t("utterance_toggle_kind")}</TooltipContent>
          </Tooltip>
         {isDialogue && (
            <Input
              value={speaker}
              list={speakerListId}
              disabled={disabled}
              onChange={(e) => onUpdate({ kind: "dialogue", speaker: e.target.value, text: value.text })}
              placeholder={t("speaker_placeholder")}
              aria-label={t("speaker_placeholder")}
              aria-invalid={speakerMissing}
              className="w-28"
            />
          )}

          <span className="flex-1" />

          <TooltipIconButton
            label={t("utterance_move_up")}
            disabled={disabled || index === 0}
            onClick={() => onMove(-1)}
            size="icon-xs"
          >
            <ChevronUp aria-hidden />
          </TooltipIconButton>
          <TooltipIconButton
            label={t("utterance_move_down")}
            disabled={disabled || index === total - 1}
            onClick={() => onMove(1)}
            size="icon-xs"
          >
            <ChevronDown aria-hidden />
          </TooltipIconButton>
          <TooltipIconButton label={t("utterance_remove")} disabled={disabled} onClick={onRemove} size="icon-xs">
            <X aria-hidden />
          </TooltipIconButton>
        </div>

        <Textarea
          value={value.text}
          disabled={disabled}
          onChange={(e) => {
            const text = e.target.value;
            onUpdate(isDialogue ? { kind: "dialogue", speaker, text } : { kind: "voiceover", speaker: null, text });
          }}
          placeholder={isDialogue ? t("utterance_dialogue_placeholder") : t("utterance_voiceover_placeholder")}
          aria-label={isDialogue ? t("utterance_kind_dialogue") : t("utterance_kind_voiceover")}
          className="min-h-8"
        />
      </div>
    </div>
  );
}

/**
 * Drama 分镜级有序发声序列的富编辑器。
 * 台词（带说话人）与画外音（无说话人）按时序排在同一序列，插入顺序即幕内先后；
 * 支持增 / 删 / 改文本与说话人 / 切换类型 / 上下移调整顺序。
 */
export function UtteranceListEditor({
  utterances,
  onChange,
  disabled = false,
  speakerCandidates,
}: UtteranceListEditorProps) {
  const { t } = useTranslation("dashboard");
  const listId = useId();
  const speakerListId = speakerCandidates?.length ? listId : undefined;

  // 数据模型无 id：在编辑态派生与条目一一绑定的稳定 key，增删移动时同步搬运，
  // 使受控输入节点按条目（而非按位置）复用，避免删除中间项 / 移动后焦点跳行、编辑内容串到相邻行。
  const [keys, setKeys] = useState<string[]>(() => utterances.map((_, i) => `u${i}`));

  // 外部整体替换（挂载后 adopt / revision 静默刷新）导致条目数与 key 数漂移时对齐：按位复用已有 key、
  // 尾部补新 key、裁掉多余。本地增删移动已同步搬运 key，长度恒等，不触发此分支。
  let renderKeys = keys;
  if (keys.length !== utterances.length) {
    renderKeys = keys.slice(0, utterances.length);
    while (renderKeys.length < utterances.length) renderKeys.push(nextKey(renderKeys));
    setKeys(renderKeys);
  }

  const updateAt = (index: number, next: Utterance) => {
    onChange(utterances.map((u, i) => (i === index ? next : u)));
  };

  const removeAt = (index: number) => {
    onChange(utterances.filter((_, i) => i !== index));
    setKeys((prev) => prev.filter((_, i) => i !== index));
  };

  const moveAt = (index: number, delta: -1 | 1) => {
    const target = index + delta;
    if (target < 0 || target >= utterances.length) return;
    const next = [...utterances];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
    setKeys((prev) => {
      const swapped = [...prev];
      [swapped[index], swapped[target]] = [swapped[target], swapped[index]];
      return swapped;
    });
  };

  const add = (kind: UtteranceKind) => {
    onChange([...utterances, makeUtterance(kind)]);
    setKeys((prev) => [...prev, nextKey(prev)]);
  };

  return (
    <div className="flex flex-col gap-1.5">
      {utterances.length === 0 ? (
        <p className="py-1 text-xs text-muted-foreground">{t("utterance_empty")}</p>
      ) : (
        <div role="list">
          {utterances.map((u, i) => (
            <div key={renderKeys[i]} role="listitem">
              <UtteranceRow
                value={u}
                index={i}
                total={utterances.length}
                disabled={disabled}
                speakerListId={speakerListId}
                onUpdate={(next) => updateAt(i, next)}
                onMove={(delta) => moveAt(i, delta)}
                onRemove={() => removeAt(i)}
              />
            </div>
          ))}
        </div>
      )}

      {speakerListId && (
        <datalist id={speakerListId}>
          {speakerCandidates?.map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
      )}

      <div className="flex items-center gap-1.5">
        <Button variant="ghost" size="xs" disabled={disabled} onClick={() => add("dialogue")}>
          <Plus aria-hidden data-icon="inline-start" />
          {t("utterance_add_dialogue")}
        </Button>
        <Button variant="ghost" size="xs" disabled={disabled} onClick={() => add("voiceover")}>
          <Plus aria-hidden data-icon="inline-start" />
          {t("utterance_add_voiceover")}
        </Button>
      </div>
    </div>
  );
}
