/**
 * Assistant / agent runtime type definitions.
 *
 * Maps to backend models in:
 * - server/agent_runtime/models.py (SessionMeta, SessionStatus)
 * - server/agent_runtime/event_log.py (TimelineEntry payload structure)
 * - server/agent_runtime/entry_pipeline.py (DraftState / DraftDeltaPayload)
 * - server/agent_runtime/service.py (SkillInfo, entry stream events)
 */

export type SessionStatus = "idle" | "running" | "completed" | "error" | "interrupted";

export interface SessionMeta {
  id: string;              // The sdk_session_id
  project_name: string;
  title: string;
  status: SessionStatus;
  created_at: string;
  updated_at: string;
}

/** Image attachment transport shape, matching the `images` elements in `/sessions/send` and `/sessions/{id}/rewrite`. */
export interface ImagePayload {
  data: string;
  media_type: string;
}

export interface ContentBlock {
  type:
    | "text"
    | "thinking"
    | "tool_use"
    | "tool_result"
    | "skill_invocation"
    | "task_progress"
    | "interrupt_notice"
    | "question_answer"
    | "agent_failure"
    | "compact_summary"
    | "image";
  text?: string;
  thinking?: string;
  id?: string;
  name?: string;
  input?: Record<string, unknown>;
  result?: string;
  is_error?: boolean;
  tool_use_id?: string;
  content?: string;
  // skill_invocation block fields (fixed at the write point: name and arguments only, without the full injected text)
  skill_name?: string;
  skill_args?: string;
  // Subagent subtimeline: projection groups by parent_tool_use_id and attaches it to the anchor tool_use block
  sub_turns?: Turn[];
  // Subagent status/progress associated with the anchor tool_use (folded from task_progress blocks)
  task_info?: ContentBlock;
  // image block fields
  source?: { type: "base64"; media_type: string; data: string };
  // task_progress fields
  task_id?: string;
  status?: string;
  description?: string;
  summary?: string;
  task_status?: string;
  usage?: { total_tokens?: number; tool_uses?: number; duration_ms?: number };
  // question_answer fields (AskUserQuestion replies: question → selected option)
  answers?: Record<string, string>;
  // agent_failure block (Agent failure observations fixed at the write point)
  failure?: FailureObservation;
}

export interface FailureObservation {
  version: number;
  phase: "startup" | "turn";
  timestamp: string;
  project_name: string | null;
  session_id: string | null;
  summary: {
    /**
     * Stable machine key for the one-sentence conclusion, derived only from structured evidence (see server/agent_runtime/failure_observation.py).
     * Historical events predating materialization of this field have no key and use the generic key for their phase.
     */
    key?: string;
    source: string;
    /** The SDK / upstream may add arbitrary JSON shapes; the frontend only presents them, without inferring enumerations. */
    type: unknown;
    status?: unknown;
    message: string | null;
  };
  raw: Record<string, unknown>;
}

export interface Turn {
  type: "user" | "assistant" | "system";
  content: ContentBlock[];
  uuid?: string;
  timestamp?: string;
  subtype?: string;
}

export interface PendingQuestion {
  question_id: string;
  questions: Array<{
    header?: string;
    question: string;
    options: Array<{ label: string; description: string }>;
    multiSelect: boolean;
  }>;
}

/** Session event log entry (the sole read source for the UI timeline; seq increases monotonically within a session). */
export interface TimelineEntry {
  seq: number;
  type: "user" | "assistant" | "tool_result" | "system";
  content?: ContentBlock[] | string;
  uuid?: string;
  timestamp?: string;
  /** Carried by assistant entries so drafts can be replaced by exact identity. */
  message_id?: string | null;
  /** Subagent message marker: projection groups by parent into one collapsed card in the main timeline. */
  parent_tool_use_id?: string;
  // tool_result / question_answer entry fields
  tool_use_id?: string | null;
  is_error?: boolean;
  // Subtypes fixed at the write point: system entries use task_* / interrupt / skill_invocation /
  // agent_turn_failure / subagent_outcome; user entries use question_answer / compact_summary
  subtype?: string;
  task_id?: string | null;
  description?: string;
  summary?: string | null;
  task_status?: string | null;
  usage?: { total_tokens?: number; tool_uses?: number; duration_ms?: number } | null;
  // system entry fields (skill_invocation subtype: name and arguments only)
  skill_name?: string | null;
  skill_args?: string | null;
  // question_answer entry fields (structured answers to AskUserQuestion)
  answers?: Record<string, string> | null;
  // agent_turn_failure system entry
  failure?: FailureObservation;
}

/** Queued message state: `queued` has been handed to the Agent and awaits acceptance; `unsent` was still queued when the Agent process exited, and the user must choose to send, edit, or delete it. */
export type QueuedMessageState = "queued" | "unsent";

/**
 * Queued message: a user message sent but not yet accepted into the conversation by the Agent (server memory state, excluded from the event log).
 * `id` is the uuid of the timeline user entry after acceptance.
 */
export interface QueuedMessage {
  id: string;
  content: ContentBlock[];
  timestamp?: string | null;
  state: QueuedMessageState;
}

/** Intent when withdrawing a queued message: `edit` returns its content to the input; `delete` discards it. */
export type QueuedMessageWithdrawal = "edit" | "delete";

/** Response to editing / deleting a queued message. */
export interface QueuedMessageWithdrawalResponse {
  session_id: string;
  id: string;
  /** `withdrawn`: withdrawn and removed from the queue; `accepted`: already received by the Agent and enters the conversation normally. */
  outcome: "withdrawn" | "accepted";
  /** The message to return to the input when editing and withdrawal succeed; null otherwise. */
  message: QueuedMessage | null;
}

/** Response to resending an "unsent" message: the same queued message, now back in `queued`. */
export interface QueuedMessageResendResponse {
  session_id: string;
  id: string;
  queued_message: QueuedMessage;
}

/** Response to sending a queued message immediately. */
export interface QueuedMessageSendNowResponse {
  session_id: string;
  id: string;
  /** `sent`: resubmitted for the Agent to interrupt the current turn and handle first; the message stays in the tray until accepted. `accepted`: already received by the Agent and not resent. */
  outcome: "sent" | "accepted";
}

/** Acceptance response for send and rewrite endpoints: exactly one of a queued message and an authoritative entry is present. */
export interface AcceptedMessageResponse {
  status: string;
  session_id: string;
  /** The first message of a new session, or a retry with the same client_key matching an already accepted message. */
  entry: TimelineEntry | null;
  /** A message in an existing session, awaiting Agent acceptance. */
  queued_message: QueuedMessage | null;
}

/** Server streaming preview snapshot (identified by message_id, excluded from the log). */
export interface DraftState {
  message_id: string;
  parent_tool_use_id?: string | null;
  content: ContentBlock[];
  rev?: number;
  /** Raw partial JSON accumulated for each tool_use block (the prefix for appending input_json_delta after reconnection). */
  tool_json?: Record<number, string>;
}

/** Delta SSE event payload (references message_id + block index; monotonically increasing rev is used for filtering after reconnection). */
export interface DraftDeltaPayload {
  message_id: string;
  parent_tool_use_id?: string | null;
  delta_type: "block_start" | "text_delta" | "thinking_delta" | "input_json_delta";
  block_index: number;
  rev: number;
  block?: ContentBlock;
  text?: string;
  thinking?: string;
  partial_json?: string;
}

/** GET /sessions/{id}/entries response. */
export interface EntriesResponse {
  session_id: string;
  status: SessionStatus;
  entries: TimelineEntry[];
  draft: DraftState | null;
  draft_rev: number;
}

export interface SkillInfo {
  name: string;
  description: string;
  scope: "project" | "user";
  path: string;
  // Backend hint of a Lucide icon id; display name lives in i18n (dashboard:skill_name_<id>).
  icon?: string;
}

export interface TodoItem {
  content: string;
  activeForm: string;
  status: "pending" | "in_progress" | "completed";
}
