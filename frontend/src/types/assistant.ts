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
  id: string;              // 现在就是 sdk_session_id
  project_name: string;
  title: string;
  status: SessionStatus;
  created_at: string;
  updated_at: string;
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
  // skill_invocation 块字段（写入点定型，只有名与入参，无注入全文）
  skill_name?: string;
  skill_args?: string;
  // subagent 子时间线：投影按 parent_tool_use_id 归组后挂在锚点 tool_use 块
  sub_turns?: Turn[];
  // 关联到锚点 tool_use 的子任务状态/进度（由 task_progress 块折叠而来）
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
  // question_answer fields（AskUserQuestion 答复：问题 → 所选选项）
  answers?: Record<string, string>;
  // agent_failure block（写入点定型的 Agent 故障观测）
  failure?: FailureObservation;
}

export interface FailureObservation {
  version: number;
  phase: "startup" | "turn";
  timestamp: string;
  project_name: string | null;
  session_id: string | null;
  summary: {
    source: string;
    /** SDK / 上游可新增任意 JSON 形态；前端只呈现，不做枚举推断。 */
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

/** 会话事件日志条目（UI 时间线唯一读源；seq 为会话内单调序号）。 */
export interface TimelineEntry {
  seq: number;
  type: "user" | "assistant" | "tool_result" | "system";
  content?: ContentBlock[] | string;
  uuid?: string;
  timestamp?: string;
  /** assistant 条目携带，供 draft 按身份精确替换。 */
  message_id?: string | null;
  /** subagent 消息标记：投影按 parent 归组为主时间线单一折叠卡片。 */
  parent_tool_use_id?: string;
  // tool_result / question_answer 条目字段
  tool_use_id?: string | null;
  is_error?: boolean;
  // 写入点定型的子类型：system 条目为 task_* / interrupt / skill_invocation；
  // user 条目为 question_answer
  subtype?: string;
  task_id?: string | null;
  description?: string;
  summary?: string | null;
  task_status?: string | null;
  usage?: { total_tokens?: number; tool_uses?: number; duration_ms?: number } | null;
  // system 条目字段（skill_invocation 子类型：只记名与入参）
  skill_name?: string | null;
  skill_args?: string | null;
  // question_answer 条目字段（AskUserQuestion 答复的结构化答案）
  answers?: Record<string, string> | null;
  // agent_turn_failure 系统条目
  failure?: FailureObservation;
}

/** 服务端流式预览态快照（身份为 message_id，不入日志）。 */
export interface DraftState {
  message_id: string;
  parent_tool_use_id?: string | null;
  content: ContentBlock[];
  rev?: number;
  /** 各 tool_use 块已累积的原始 partial JSON（重连后续拼 input_json_delta 的前缀）。 */
  tool_json?: Record<number, string>;
}

/** delta SSE 事件载荷（引用 message_id + block index，rev 单调用于重连过滤）。 */
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

/** GET /sessions/{id}/entries 响应。 */
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
