import type { ImagePayload, SessionStatus, Turn } from "@/types";

// ---------------------------------------------------------------------------
// TERMINAL_SESSION_STATUSES – session statuses treated as "done" for the
// purpose of freezing running/pending indicators (subagent cards, task rows).
// ---------------------------------------------------------------------------

export const TERMINAL_SESSION_STATUSES = new Set(["completed", "error", "interrupted"]);

// ---------------------------------------------------------------------------
// composeAllTurns – merge live draft into committed turn list for rendering.
//
// User interruptions leave interrupted assistant streaming contents in draftTurn (unfinished
// messages produce no authoritative log entries). turns then ends with interrupt_notice system
// turns; appending drafts would render Interrupt → Agent reply, reversing chronological
// order. Insert drafts before interrupt_notice to show
// Agent reply → Interrupt. Refresh naturally removes drafts (server memory only, never logged).
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// turnPlainText: copyable/rewritable turn bodies, text blocks only.
// ---------------------------------------------------------------------------

export function turnPlainText(turn: Turn): string {
  return (turn.content ?? [])
    .filter((block) => block.type === "text" && typeof block.text === "string")
    .map((block) => block.text)
    .join("\n\n");
}

// ---------------------------------------------------------------------------
// turnImageAttachments: turn image attachments reconstructed in sender transport shapes.
//
// Image blocks persist unchanged as base64 log entries; rewrites retrieve anchor-message images and submit alongside new messages
// using ordinary image-send request shapes. Block order is submission order (server images first, text last).
// ---------------------------------------------------------------------------

export function turnImageAttachments(turn: Turn): ImagePayload[] {
  return (turn.content ?? []).flatMap((block) => {
    const source = block.type === "image" ? block.source : undefined;
    if (!source?.data) return [];
    return [{ data: source.data, media_type: source.media_type }];
  });
}

// ---------------------------------------------------------------------------
// canEditUserTurn: whether rewrite entries are currently available.
//
// Client prechecks; servers remain authoritative (invalid anchors 400, unresolved questionnaires 409).
// Uneditable entries are omitted rather than disabled.
// ---------------------------------------------------------------------------

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
  // Rewrite anchors are entry UUIDs; UUID-less turns (synthetic cards/drafts) cannot be anchored.
  if (!turn.uuid) return false;
  // Questionnaire answers are Agent-questionnaire receipts, not user-authored messages. Projected Turns lack
  // subtype, so identify through content-block types.
  if ((turn.content ?? []).some((block) => block.type === "question_answer")) return false;
  if (!turnPlainText(turn).trim() && turnImageAttachments(turn).length === 0) return false;
  if (context.sessionStatus === "running") return false;
  if (context.hasPendingQuestion) return false;
  // Pending sends/rewrites: allowing other Edit entries now would replace submitting editors
  // and lose their unaccepted drafts.
  if (context.isSending) return false;
  // Queued messages have not entered conversations; forking now would strand them in replaced original sessions.
  if (context.hasQueuedMessages) return false;
  return true;
}

/** Tool-result display text: strings unchanged, other values pretty-printed JSON. */
export function stringifyResult(value: unknown): string {
  if (value === undefined || value === null) return "";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}
