/**
 * Assistant / agent runtime type definitions.
 *
 * Maps to backend models in:
 * - webui/server/agent_runtime/models.py (SessionMeta, SessionStatus, AssistantSnapshotV2)
 * - webui/server/agent_runtime/turn_grouper.py (Turn, ContentBlock structure)
 * - webui/server/agent_runtime/service.py (SkillInfo, stream events)
 */

export type SessionStatus = "idle" | "running" | "completed" | "error" | "interrupted";

export interface SessionMeta {
  id: string;
  sdk_session_id: string | null;
  project_name: string;
  title: string;
  status: SessionStatus;
  created_at: string;
  updated_at: string;
}

export interface ContentBlock {
  type: "text" | "thinking" | "tool_use" | "tool_result" | "skill_content";
  text?: string;
  thinking?: string;
  id?: string;
  name?: string;
  input?: Record<string, unknown>;
  result?: string;
  is_error?: boolean;
  skill_content?: string;
  tool_use_id?: string;
  content?: string;
}

export interface Turn {
  type: "user" | "assistant" | "result" | "system";
  content: ContentBlock[];
  uuid?: string;
  timestamp?: string;
  subtype?: string;
}

export interface PendingQuestion {
  question_id: string;
  questions: Array<{
    question: string;
    options: Array<{ label: string; description: string }>;
    multiSelect: boolean;
  }>;
}

export interface AssistantSnapshot {
  session_id: string;
  status: SessionStatus;
  turns: Turn[];
  draft_turn: Turn | null;
  pending_questions: PendingQuestion[];
}

export interface SkillInfo {
  name: string;
  description: string;
  scope: "project" | "user";
  path: string;
}
