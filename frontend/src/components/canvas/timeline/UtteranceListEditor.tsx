import { useTranslation } from "react-i18next";
import {
  ChevronDown,
  ChevronUp,
  MessageSquareQuote,
  Plus,
  Radio,
  X,
} from "lucide-react";
import type { Utterance, UtteranceKind } from "@/types";
import { AutoTextarea } from "@/components/ui/AutoTextarea";

interface UtteranceListEditorProps {
  utterances: Utterance[];
  onChange: (next: Utterance[]) => void;
  disabled?: boolean;
}

function makeUtterance(kind: UtteranceKind): Utterance {
  return kind === "dialogue"
    ? { kind: "dialogue", speaker: "", text: "" }
    : { kind: "voiceover", speaker: null, text: "" };
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
  onUpdate: (next: Utterance) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
}

/**
 * 一条有序发声条目：左侧时序节点（台词 = 实心、画外音 = 空心）+ 类型切换 +
 * 说话人（仅台词）+ 自适应台词文本 + 上下移 / 删除。台词缺说话人时高亮提示
 * （后端 kind ⇄ speaker 约束，保存时会拒）。
 */
function UtteranceRow({
  value,
  index,
  total,
  disabled,
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
        {index > 0 && (
          <span aria-hidden="true" className="absolute top-0 h-2.5 w-px bg-hairline" />
        )}
        <span
          aria-hidden="true"
          className={`mt-2 h-2.5 w-2.5 rounded-full ${
            isDialogue
              ? "bg-accent shadow-[0_0_8px_-1px_var(--color-accent-glow)]"
              : "border border-text-4 bg-transparent"
          }`}
        />
        {index < total - 1 && (
          <span aria-hidden="true" className="w-px flex-1 bg-hairline" />
        )}
      </div>

      <div className="min-w-0 flex-1 pb-2.5">
        <div className="mb-1 flex items-center gap-1.5">
          <button
            type="button"
            disabled={disabled}
            onClick={() => onUpdate(flipKind(value))}
            title={t("utterance_toggle_kind")}
            className="focus-ring inline-flex items-center gap-1 rounded-md border border-hairline bg-bg-grad-a/55 px-1.5 py-0.5 text-[11px] text-text-3 transition-colors hover:border-hairline-strong hover:text-text disabled:opacity-50"
          >
            {isDialogue ? (
              <MessageSquareQuote className="h-3 w-3" />
            ) : (
              <Radio className="h-3 w-3" />
            )}
            {isDialogue ? t("utterance_kind_dialogue") : t("utterance_kind_voiceover")}
          </button>

          {isDialogue && (
            <input
              type="text"
              value={speaker}
              disabled={disabled}
              onChange={(e) => onUpdate({ kind: "dialogue", speaker: e.target.value, text: value.text })}
              placeholder={t("speaker_placeholder")}
              aria-label={t("speaker_placeholder")}
              aria-invalid={speakerMissing}
              className={`focus-ring w-24 rounded-md border bg-bg-grad-a/55 px-2 py-0.5 text-[12px] text-text transition-colors disabled:opacity-50 ${
                speakerMissing ? "border-amber-500/70" : "border-hairline hover:border-hairline-strong"
              }`}
            />
          )}

          <span className="flex-1" />

          <button
            type="button"
            disabled={disabled || index === 0}
            onClick={() => onMove(-1)}
            aria-label={t("utterance_move_up")}
            title={t("utterance_move_up")}
            className="focus-ring rounded p-0.5 text-text-4 transition-colors enabled:hover:text-text disabled:opacity-30"
          >
            <ChevronUp className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            disabled={disabled || index === total - 1}
            onClick={() => onMove(1)}
            aria-label={t("utterance_move_down")}
            title={t("utterance_move_down")}
            className="focus-ring rounded p-0.5 text-text-4 transition-colors enabled:hover:text-text disabled:opacity-30"
          >
            <ChevronDown className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            disabled={disabled}
            onClick={onRemove}
            aria-label={t("utterance_remove")}
            title={t("utterance_remove")}
            className="focus-ring rounded p-0.5 text-text-4 transition-colors enabled:hover:text-rose-400 disabled:opacity-30"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        <AutoTextarea
          value={value.text}
          disabled={disabled}
          onChange={(text) =>
            onUpdate(isDialogue ? { kind: "dialogue", speaker, text } : { kind: "voiceover", speaker: null, text })
          }
          placeholder={isDialogue ? t("utterance_dialogue_placeholder") : t("utterance_voiceover_placeholder")}
          aria-label={isDialogue ? t("utterance_kind_dialogue") : t("utterance_kind_voiceover")}
          className={value.kind === "voiceover" ? "italic text-text-2" : ""}
        />
      </div>
    </div>
  );
}

/**
 * Drama 场景级有序发声序列的富编辑器（A1：从旧扁平 dialogue 编辑迁出并升级）。
 * 台词（带说话人）与画外音（无说话人）按时序排在同一序列，插入顺序即幕内先后；
 * 支持增 / 删 / 改文本与说话人 / 切换类型 / 上下移调整顺序。
 */
export function UtteranceListEditor({ utterances, onChange, disabled = false }: UtteranceListEditorProps) {
  const { t } = useTranslation("dashboard");

  const updateAt = (index: number, next: Utterance) => {
    onChange(utterances.map((u, i) => (i === index ? next : u)));
  };

  const removeAt = (index: number) => {
    onChange(utterances.filter((_, i) => i !== index));
  };

  const moveAt = (index: number, delta: -1 | 1) => {
    const target = index + delta;
    if (target < 0 || target >= utterances.length) return;
    const next = [...utterances];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };

  const add = (kind: UtteranceKind) => {
    onChange([...utterances, makeUtterance(kind)]);
  };

  return (
    <div className="flex flex-col gap-1.5">
      {utterances.length === 0 ? (
        <p className="py-1 text-[12px] text-text-4">{t("utterance_empty")}</p>
      ) : (
        <div role="list">
          {utterances.map((u, i) => (
            <UtteranceRow
              key={i}
              value={u}
              index={i}
              total={utterances.length}
              disabled={disabled}
              onUpdate={(next) => updateAt(i, next)}
              onMove={(delta) => moveAt(i, delta)}
              onRemove={() => removeAt(i)}
            />
          ))}
        </div>
      )}

      <div className="flex items-center gap-1.5">
        <button
          type="button"
          disabled={disabled}
          onClick={() => add("dialogue")}
          className="focus-ring inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11.5px] text-text-3 transition-colors hover:bg-[oklch(1_0_0_/_0.05)] hover:text-text disabled:opacity-50"
        >
          <Plus className="h-3 w-3" />
          {t("utterance_add_dialogue")}
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() => add("voiceover")}
          className="focus-ring inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11.5px] text-text-3 transition-colors hover:bg-[oklch(1_0_0_/_0.05)] hover:text-text disabled:opacity-50"
        >
          <Plus className="h-3 w-3" />
          {t("utterance_add_voiceover")}
        </button>
      </div>
    </div>
  );
}
