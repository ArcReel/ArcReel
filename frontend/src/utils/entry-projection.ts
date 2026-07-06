/**
 * 会话事件日志投影 — entries → Turn[] 纯函数。
 *
 * 日志条目已在服务端写入点定型（tool_result 独立条目、subagent 条目带
 * parent_tool_use_id、stream_event 不入日志），本模块只做渲染归组：
 * 连续 assistant 条目合并、tool_result 按 tool_use_id 回填、task/skill/
 * 中断等过渡期通用条目映射为既有渲染块。不做内容比对去重、不合成消息。
 */

import type {
  ContentBlock,
  DraftDeltaPayload,
  DraftState,
  TimelineEntry,
  Turn,
} from "@/types";

// ---------------------------------------------------------------------------
// 过渡期通用条目识别（与后端 turn_grouper 同口径）
// ---------------------------------------------------------------------------

const SKILL_BASE_DIR_PREFIX = "Base directory for this skill:";
const SKILL_CONTENT_PREFIX = "Skill content:";
const INTERRUPT_ECHO_PREFIX = "[Request interrupted";
const TASK_NOTIFICATION_RE = /<task-notification>\s*[\s\S]*?<\/task-notification>/;

function isSkillContentText(text: string): boolean {
  return text.startsWith(SKILL_BASE_DIR_PREFIX) || text.startsWith(SKILL_CONTENT_PREFIX);
}

function entryBlocks(entry: TimelineEntry): ContentBlock[] {
  const content = entry.content;
  if (Array.isArray(content)) return content;
  if (typeof content === "string" && content) return [{ type: "text", text: content }];
  return [];
}

function blocksText(blocks: ContentBlock[]): string {
  return blocks
    .filter((b) => b.type === "text")
    .map((b) => b.text ?? "")
    .join("\n");
}

function isInterruptEcho(blocks: ContentBlock[]): boolean {
  if (blocks.length !== 1 || blocks[0].type !== "text") return false;
  return (blocks[0].text ?? "").trim().startsWith(INTERRUPT_ECHO_PREFIX);
}

interface TaskNotificationInfo {
  task_id: string;
  tool_use_id: string;
  status: string;
  summary: string;
}

function extractTaskNotification(blocks: ContentBlock[]): TaskNotificationInfo | null {
  const text = blocksText(blocks);
  const match = TASK_NOTIFICATION_RE.exec(text);
  if (!match) return null;
  const xml = match[0];
  const tag = (name: string): string => {
    const m = new RegExp(`<${name}>([\\s\\S]*?)</${name}>`).exec(xml);
    return m ? m[1].trim() : "";
  };
  return {
    task_id: tag("task-id"),
    tool_use_id: tag("tool-use-id"),
    status: tag("status"),
    summary: tag("summary"),
  };
}

// ---------------------------------------------------------------------------
// 归组辅助
// ---------------------------------------------------------------------------

function attachToolResult(turnContent: ContentBlock[], entry: TimelineEntry): void {
  const toolUseId = entry.tool_use_id;
  const resultText = typeof entry.content === "string" ? entry.content : blocksText(entryBlocks(entry));
  if (toolUseId) {
    for (const block of turnContent) {
      if (block.type === "tool_use" && block.id === toolUseId) {
        block.result = resultText;
        block.is_error = Boolean(entry.is_error);
        return;
      }
    }
  }
  turnContent.push({
    type: "tool_result",
    tool_use_id: toolUseId ?? undefined,
    content: resultText,
    is_error: Boolean(entry.is_error),
  });
}

function attachSkillText(turnContent: ContentBlock[], text: string): void {
  for (let i = turnContent.length - 1; i >= 0; i--) {
    const block = turnContent[i];
    if (block.type === "tool_use" && block.name === "Skill") {
      block.skill_content = text;
      return;
    }
  }
  turnContent.push({ type: "skill_content", text });
}

function findTaskBlock(turn: Turn | null, taskId: string): ContentBlock | null {
  if (!turn) return null;
  for (const block of turn.content) {
    if (block.type === "task_progress" && block.task_id === taskId) return block;
  }
  return null;
}

function lastTurnIsInterruptNotice(turn: Turn | null): boolean {
  if (!turn || turn.type !== "system") return false;
  const blocks = turn.content;
  return blocks.length > 0 && blocks[blocks.length - 1].type === "interrupt_notice";
}

/** task_started 块对应的 Agent tool_use 已有 result 时推导为已完成。 */
function resolveStaleTaskBlocks(turns: Turn[]): void {
  for (const turn of turns) {
    const completedToolIds = new Set<string>();
    for (const block of turn.content) {
      if (block.type === "tool_use" && block.name === "Agent" && block.result !== undefined && block.id) {
        completedToolIds.add(block.id);
      }
    }
    if (completedToolIds.size === 0) continue;
    for (const block of turn.content) {
      if (
        block.type === "task_progress" &&
        block.status === "task_started" &&
        block.tool_use_id &&
        completedToolIds.has(block.tool_use_id)
      ) {
        block.status = "task_notification";
        block.task_status = "completed";
      }
    }
  }
}

function cloneBlock(block: ContentBlock): ContentBlock {
  return structuredClone(block);
}

/**
 * 按 seq 合并两组日志条目（并集、升序、seq 去重）。日志 append-only 且条目
 * 按 seq 不可变，任一来源（冷读整帧 / SSE 直播 / 发送响应）先到后到均可安全
 * 并集，不存在覆盖语义。
 */
export function mergeEntriesBySeq(
  existing: TimelineEntry[],
  incoming: TimelineEntry[],
): TimelineEntry[] {
  if (existing.length === 0) return [...incoming].sort((a, b) => a.seq - b.seq);
  if (incoming.length === 0) return existing;
  const bySeq = new Map<number, TimelineEntry>();
  for (const entry of existing) bySeq.set(entry.seq, entry);
  for (const entry of incoming) {
    if (!bySeq.has(entry.seq)) bySeq.set(entry.seq, entry);
  }
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}

// ---------------------------------------------------------------------------
// projectEntriesToTurns — 主投影
// ---------------------------------------------------------------------------

export function projectEntriesToTurns(entries: TimelineEntry[]): Turn[] {
  const turns: Turn[] = [];
  // 用持有器承载当前 turn：闭包内赋值会让 TS 把裸 let 收窄成 never
  const cursor: { current: Turn | null } = { current: null };

  const flush = (): void => {
    if (cursor.current) {
      turns.push(cursor.current);
      cursor.current = null;
    }
  };

  const startTurn = (turn: Turn): void => {
    flush();
    cursor.current = turn;
  };

  /** 追加系统注入块：优先并入当前 assistant turn，否则开 system turn。 */
  const attachSystemBlock = (entry: TimelineEntry, block: ContentBlock): void => {
    if (cursor.current && cursor.current.type === "assistant") {
      cursor.current.content.push(block);
      return;
    }
    startTurn({ type: "system", content: [block], uuid: entry.uuid, timestamp: entry.timestamp });
  };

  const applyTaskBlock = (entry: TimelineEntry, taskBlock: ContentBlock, updateOnly: boolean): void => {
    const taskId = taskBlock.task_id;
    if (taskId && updateOnly) {
      const existing = findTaskBlock(cursor.current, taskId);
      if (existing) {
        existing.status = taskBlock.status;
        if (taskBlock.summary) existing.summary = taskBlock.summary;
        if (taskBlock.task_status) existing.task_status = taskBlock.task_status;
        if (taskBlock.usage) existing.usage = taskBlock.usage;
        return;
      }
    }
    attachSystemBlock(entry, taskBlock);
  };

  for (const entry of entries) {
    if (entry.type === "assistant") {
      const blocks = entryBlocks(entry).map(cloneBlock);
      if (cursor.current && cursor.current.type === "assistant") {
        cursor.current.content.push(...blocks);
      } else {
        startTurn({ type: "assistant", content: blocks, uuid: entry.uuid, timestamp: entry.timestamp });
      }
      continue;
    }

    if (entry.type === "tool_result") {
      if (cursor.current && cursor.current.type === "assistant") {
        attachToolResult(cursor.current.content, entry);
      } else {
        attachSystemBlock(entry, {
          type: "tool_result",
          tool_use_id: entry.tool_use_id ?? undefined,
          content: typeof entry.content === "string" ? entry.content : blocksText(entryBlocks(entry)),
          is_error: Boolean(entry.is_error),
        });
      }
      continue;
    }

    if (entry.type === "system") {
      if (entry.subtype !== "task_started" && entry.subtype !== "task_progress" && entry.subtype !== "task_notification") {
        continue;
      }
      applyTaskBlock(
        entry,
        {
          type: "task_progress",
          task_id: entry.task_id ?? undefined,
          status: entry.subtype,
          description: entry.description ?? "",
          summary: entry.summary ?? undefined,
          task_status: entry.task_status ?? undefined,
          usage: entry.usage ?? undefined,
          tool_use_id: entry.tool_use_id ?? undefined,
        },
        entry.subtype !== "task_started",
      );
      continue;
    }

    // entry.type === "user"
    const blocks = entryBlocks(entry).map(cloneBlock);

    if (isInterruptEcho(blocks)) {
      if (lastTurnIsInterruptNotice(cursor.current)) continue;
      startTurn({
        type: "system",
        content: [{ type: "interrupt_notice" }],
        uuid: entry.uuid,
        timestamp: entry.timestamp,
      });
      continue;
    }

    const taskInfo = extractTaskNotification(blocks);
    if (taskInfo) {
      applyTaskBlock(
        entry,
        {
          type: "task_progress",
          task_id: taskInfo.task_id || undefined,
          status: "task_notification",
          description: "",
          summary: taskInfo.summary || undefined,
          task_status: taskInfo.status || undefined,
          tool_use_id: taskInfo.tool_use_id || undefined,
        },
        Boolean(taskInfo.task_id),
      );
      continue;
    }

    if (entry.parent_tool_use_id) {
      // subagent 内部注入：抑制纯文本（内部 prompt/遥测），其余块并入。
      const filtered = blocks.filter((b) => b.type !== "text" || isSkillContentText((b.text ?? "").trim()));
      if (filtered.length === 0) continue;
      for (const block of filtered) {
        if (block.type === "text") {
          if (cursor.current && cursor.current.type === "assistant") {
            attachSkillText(cursor.current.content, (block.text ?? "").trim());
          } else {
            attachSystemBlock(entry, { type: "skill_content", text: (block.text ?? "").trim() });
          }
        } else {
          attachSystemBlock(entry, block);
        }
      }
      continue;
    }

    const nonEmpty = blocks.filter((b) => b.type !== "text" || (b.text ?? "").trim() !== "");
    const allSkillText = nonEmpty.length > 0 && nonEmpty.every(
      (b) => b.type === "text" && isSkillContentText((b.text ?? "").trim()),
    );
    if (allSkillText) {
      for (const block of nonEmpty) {
        const text = (block.text ?? "").trim();
        if (cursor.current && cursor.current.type === "assistant") {
          attachSkillText(cursor.current.content, text);
        } else {
          attachSystemBlock(entry, { type: "skill_content", text });
        }
      }
      continue;
    }

    startTurn({ type: "user", content: blocks, uuid: entry.uuid, timestamp: entry.timestamp });
  }

  flush();
  resolveStaleTaskBlocks(turns);
  return turns;
}

// ---------------------------------------------------------------------------
// draft 投影与增量应用
// ---------------------------------------------------------------------------

/**
 * draft → Turn。完成替换按身份比对：日志中已有同 message_id 的 assistant
 * 条目时 draft 视为已被权威条目替换，返回 null。不做内容比对。
 */
export function projectDraftToTurn(
  draft: DraftState | null,
  entries: TimelineEntry[],
): Turn | null {
  if (!draft || !draft.message_id) return null;
  const replaced = entries.some(
    (e) => e.type === "assistant" && e.message_id != null && e.message_id === draft.message_id,
  );
  if (replaced) return null;
  const content = draft.content.filter(Boolean);
  if (content.length === 0) return null;
  return {
    type: "assistant",
    content,
    uuid: `draft-${draft.message_id}`,
  };
}

/** 客户端 draft 镜像：content 以 block_index 为数组下标，toolJson 累积未闭合 JSON。 */
export interface DraftMirror extends DraftState {
  toolJson: Record<number, string>;
}

/**
 * 应用一条 delta（纯函数，返回新对象）。调用方须先按 rev 门槛过滤；
 * message_id 变化时另起新 draft（身份切换即上一条已由权威条目收尾）。
 */
export function applyDraftDelta(
  draft: DraftMirror | null,
  payload: DraftDeltaPayload,
): DraftMirror {
  const base: DraftMirror =
    draft && draft.message_id === payload.message_id
      ? { ...draft, content: [...draft.content], toolJson: { ...draft.toolJson } }
      : {
          message_id: payload.message_id,
          parent_tool_use_id: payload.parent_tool_use_id ?? null,
          content: [],
          toolJson: {},
        };
  const index = payload.block_index;

  if (payload.delta_type === "block_start") {
    base.content[index] = payload.block ? cloneBlock(payload.block) : { type: "text", text: "" };
    return base;
  }

  if (payload.delta_type === "text_delta") {
    const block = base.content[index]?.type === "text" ? { ...base.content[index] } : { type: "text" as const, text: "" };
    block.text = `${block.text ?? ""}${payload.text ?? ""}`;
    base.content[index] = block;
    return base;
  }

  if (payload.delta_type === "thinking_delta") {
    const block =
      base.content[index]?.type === "thinking" ? { ...base.content[index] } : { type: "thinking" as const, thinking: "" };
    block.thinking = `${block.thinking ?? ""}${payload.thinking ?? ""}`;
    base.content[index] = block;
    return base;
  }

  if (payload.delta_type === "input_json_delta") {
    const block =
      base.content[index]?.type === "tool_use"
        ? { ...base.content[index] }
        : { type: "tool_use" as const, id: undefined, name: "", input: {} };
    const updated = `${base.toolJson[index] ?? ""}${payload.partial_json ?? ""}`;
    base.toolJson[index] = updated;
    try {
      const parsed: unknown = JSON.parse(updated);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        block.input = parsed as Record<string, unknown>;
      }
    } catch {
      // 未闭合 JSON：继续累积
    }
    base.content[index] = block;
    return base;
  }

  return base;
}
