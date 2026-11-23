import type { ContentBlock, Turn } from "@/types";
import { composeAllTurns } from "./utils";


export interface DisplayItem {
  key: string;
  turn: Turn;
  streaming: boolean;
}

export function buildDisplayItems(turns: Turn[], draftTurn: Turn | null): DisplayItem[] {
  const items: DisplayItem[] = [];
  composeAllTurns(turns, draftTurn).forEach((composed, index) => {
    const streaming = composed === draftTurn;
    if (!streaming && !hasVisibleContent(composed)) return;
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
