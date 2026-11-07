import type { ContentBlock, Turn } from "@/types";
import { composeAllTurns } from "./utils";

// ---------------------------------------------------------------------------
// Message display layers convert projected turn lists into individually rendered items.
//
// SDK-message projections often split single replies into multiple assistant turns, many containing only signed empty thinking blocks;
// streaming drafts are separate too. Display layers merge consecutive assistant turns between user messages into reply rounds,
// grouping work/copy by entire rounds and skipping invisible turns/blocks without empty entries/extra spacing.
// ---------------------------------------------------------------------------

export interface DisplayItem {
  /** Render/scroll identity; reply-round identities derive from preceding items and stay stable before/after draft persistence. */
  key: string;
  /** Merged turns concatenate reply blocks chronologically, using final-part timestamps. */
  turn: Turn;
  /** Items containing streaming drafts with final blocks still generating. */
  streaming: boolean;
}

export function buildDisplayItems(turns: Turn[], draftTurn: Turn | null): DisplayItem[] {
  const items: DisplayItem[] = [];
  composeAllTurns(turns, draftTurn).forEach((composed, index) => {
    const streaming = composed === draftTurn;
    if (!streaming && !hasVisibleContent(composed)) return;
    // Remove invisible blocks from persisted replies; signed empty thinking blocks would become empty work paragraphs with extra gaps.
    const turn = composed.type === "assistant" && !streaming
      ? { ...composed, content: composed.content.filter(isVisibleBlock) }
      : composed;

    const previous = items.at(-1);
    if (turn.type === "assistant" && previous?.turn.type === "assistant") {
      previous.turn = {
        ...previous.turn,
        content: [...previous.turn.content, ...turn.content],
        timestamp: turn.timestamp ?? previous.turn.timestamp,
      };
      previous.streaming ||= streaming;
      return;
    }

    // Reply rounds follow user messages/system events, anchored to preceding items: persistence changes first turns into
    // entry UUIDs; adopting those identities would remount entire rounds and collapse expanded work.
    const key = turn.type === "assistant"
      ? `${previous?.key ?? "start"}:reply`
      : (turn.uuid ?? `turn-${index}`);
    items.push({ key, turn, streaming });
  });
  return items;
}

function hasVisibleContent(turn: Turn): boolean {
  return (turn.content ?? []).some(isVisibleBlock);
}

function isVisibleBlock(block: ContentBlock): boolean {
  switch (block.type) {
    case "text":
      return Boolean(block.text?.trim());
    case "thinking":
      return Boolean(block.thinking?.trim());
    case "image":
      return Boolean(block.source?.data);
    case "agent_failure":
      return Boolean(block.failure);
    default:
      return true;
  }
}
