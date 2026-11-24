
export type SessionStatus = "idle" | "running" | "completed" | "error" | "interrupted";

export interface SessionMeta {
  id: string;
  project_name: string;
  title: string;
  status: SessionStatus;
  created_at: string;
  updated_at: string;
}

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
  skill_name?: string;
  skill_args?: string;
  sub_turns?: Turn[];
  task_info?: ContentBlock;
  source?: { type: "base64"; media_type: string; data: string };
  task_id?: string;
  status?: string;
  description?: string;
  summary?: string;
  task_status?: string;
  usage?: { total_tokens?: number; tool_uses?: number; duration_ms?: number };
  answers?: Record<string, string>;
  failure?: FailureObservation;
}

export interface FailureObservation {
  version: number;
  phase: "startup" | "turn";
  timestamp: string;
  project_name: string | null;
  session_id: string | null;
  summary: {
    key?: string;
    source: string;
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

export interface TimelineEntry {
  seq: number;
  type: "user" | "assistant" | "tool_result" | "system";
  content?: ContentBlock[] | string;
  uuid?: string;
  timestamp?: string;
  message_id?: string | null;
  parent_tool_use_id?: string;
  tool_use_id?: string | null;
  is_error?: boolean;
  subtype?: string;
  task_id?: string | null;
  description?: string;
  summary?: string | null;
  task_status?: string | null;
  usage?: { total_tokens?: number; tool_uses?: number; duration_ms?: number } | null;
  skill_name?: string | null;
  skill_args?: string | null;
  answers?: Record<string, string> | null;
  failure?: FailureObservation;
}

export type QueuedMessageState = "queued" | "unsent";

export interface QueuedMessage {
  id: string;
  content: ContentBlock[];
  timestamp?: string | null;
  state: QueuedMessageState;
}

export type QueuedMessageWithdrawal = "edit" | "delete";

export interface QueuedMessageWithdrawalResponse {
  session_id: string;
  id: string;
  outcome: "withdrawn" | "accepted";
  message: QueuedMessage | null;
}

export interface QueuedMessageResendResponse {
  session_id: string;
  id: string;
  queued_message: QueuedMessage;
}

export interface QueuedMessageSendNowResponse {
  session_id: string;
  id: string;
  outcome: "sent" | "accepted";
}

export interface AcceptedMessageResponse {
  status: string;
  session_id: string;
  entry: TimelineEntry | null;
  queued_message: QueuedMessage | null;
}

export interface DraftState {
  message_id: string;
  parent_tool_use_id?: string | null;
  content: ContentBlock[];
  rev?: number;
  tool_json?: Record<number, string>;
}

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
  icon?: string;
}

export interface TodoItem {
  content: string;
  activeForm: string;
  status: "pending" | "in_progress" | "completed";
}
