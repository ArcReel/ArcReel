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
  /** Speaker candidates; free-form names remain allowed for extras without voice binding. */
  speakerCandidates?: readonly string[];
}

function makeUtterance(kind: UtteranceKind): Utterance {
  return kind === "dialogue"
    ? { kind: "dialogue", speaker: "", text: "" }
    : { kind: "voiceover", speaker: null, text: "" };
}

/** Next stable key: largest numeric suffix +1, empty starts u0 to match initialization.
 *  Pure, idempotent under StrictMode double calls. */
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
  /** Speaker-candidate datalist id, undefined without candidates. */
  speakerListId?: string;
  onUpdate: (next: Utterance) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
}

/**
 * Ordered speech entry: timing node left (solid dialogue/hollow voiceover), type toggle, speaker for dialogue,
 * expanding text, move up/down/delete. Dialogue without speakers is invalid (backend kind ⇄ speaker constraints reject saves).
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
      {/* Timing line/nodes: solid dialogue, hollow voiceover. */}
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
 * Rich editor for drama shot-level ordered speech.
 * Dialogue with speakers/voiceover without speakers share one sequence; insertion order is in-shot chronology.
 * Add/remove/edit text/speakers, switch kinds, and move up/down.
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

  // Models lack IDs; derive stable entry-bound keys and move them with add/remove/reorder,
  // reusing controlled inputs by entry rather than position, avoiding focus jumps/content leaks after deletion/moves.
  const [keys, setKeys] = useState<string[]>(() => utterances.map((_, i) => `u${i}`));

  // External replacement (postmount adopt/silent revision refresh) may mismatch entry/key counts; reuse existing keys positionally,
  // append new ones, truncate extras. Local mutations already move keys with equal lengths and bypass this branch.
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
        <ul>
          {utterances.map((u, i) => (
            <li key={renderKeys[i]}>
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
            </li>
          ))}
        </ul>
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
