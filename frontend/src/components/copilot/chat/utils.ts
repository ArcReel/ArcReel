import type { ImagePayload, SessionStatus, Turn } from "@/types";


export const TERMINAL_SESSION_STATUSES = new Set(["completed", "error", "interrupted"]);


export function composeAllTurns(turns: Turn[], draftTurn: Turn | null): Turn[] {
  if (!draftTurn) return turns;
  const last = turns.at(-1);
  const lastIsInterrupt = last?.type === "system"
    && (last.content ?? []).some((b) => b.type === "interrupt_notice");
  if (lastIsInterrupt && last) {
    return [...turns.slice(0, -1), draftTurn, last];
  }
  return [...turns, draftTurn];
}


export function turnPlainText(turn: Turn): string {
  return (turn.content ?? [])
    .filter((block) => block.type === "text" && typeof block.text === "string")
    .map((block) => block.text)
    .join("\n\n");
}


export function turnImageAttachments(turn: Turn): ImagePayload[] {
  return (turn.content ?? []).flatMap((block) => {
    const source = block.type === "image" ? block.source : undefined;
    if (!source?.data) return [];
    return [{ data: source.data, media_type: source.media_type }];
  });
}


export function canEditUserTurn(
  turn: Turn,
  context: {
    sessionStatus: SessionStatus | null;
    hasPendingQuestion: boolean;
    isSending: boolean;
    hasQueuedMessages: boolean;
  },
): boolean {
  if (turn.type !== "user") return false;
  if (!turn.uuid) return false;
  if ((turn.content ?? []).some((block) => block.type === "question_answer")) return false;
  if (!turnPlainText(turn).trim() && turnImageAttachments(turn).length === 0) return false;
  if (context.sessionStatus === "running") return false;
  if (context.hasPendingQuestion) return false;
  if (context.isSending) return false;
  if (context.hasQueuedMessages) return false;
  return true;
}

export function stringifyResult(value: unknown): string {
  if (value === undefined || value === null) return "";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}
