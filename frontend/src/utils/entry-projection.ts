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

const INTERRUPT_ECHO_PREFIX = "[Request interrupted";
const TASK_NOTIFICATION_RE = /<task-notification>\s*[\s\S]*?<\/task-notification>/;

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

/**
 * task_progress 块折叠进同 turn 的锚点 tool_use：子任务卡片就地显示
 * 状态与进度，不再渲染独立进度行。无锚点的 task 块保持原样。
 */
function foldTaskBlocksIntoAnchors(turns: Turn[]): void {
  for (const turn of turns) {
    const toolUseById = new Map<string, ContentBlock>();
    for (const block of turn.content) {
      if (block.type === "tool_use" && block.id) toolUseById.set(block.id, block);
    }
    if (toolUseById.size === 0) continue;
    turn.content = turn.content.filter((block) => {
      if (block.type !== "task_progress" || !block.tool_use_id) return true;
      const anchor = toolUseById.get(block.tool_use_id);
      if (!anchor) return true;
      anchor.task_info = block;
      return false;
    });
  }
}

/** 在既有 turns 中查找指定 id 的 tool_use 块（subagent 卡片锚点）。 */
function findToolUseBlock(turns: Turn[], toolUseId: string): ContentBlock | null {
  for (const turn of turns) {
    for (const block of turn.content) {
      if (block.type === "tool_use" && block.id === toolUseId) return block;
    }
  }
  return null;
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

/**
 * 按 parent_tool_use_id 归组 subagent 条目并投影为子时间线：
 * 子组递归投影为 Turn[]，挂到锚点 tool_use 块的 sub_turns；
 * 主时间线只保留单一卡片锚点，不平铺 subagent 内部消息。
 *
 * 锚点可能落在主时间线，也可能落在另一子组自己的子时间线内——subagent
 * 自身再起 subagent 时，内层子组的锚点 tool_use 只存在于外层子组的
 * 子时间线中。因此锚点定位在“主时间线 ∪ 全部子组”范围内查找，不限于
 * 主时间线；子组各自独立投影、互不依赖顺序，任意嵌套深度一次收敛。
 */
export function projectEntriesToTurns(entries: TimelineEntry[]): Turn[] {
  const subgroups = new Map<string, TimelineEntry[]>();
  const mainEntries: TimelineEntry[] = [];
  for (const entry of entries) {
    if (entry.parent_tool_use_id) {
      const group = subgroups.get(entry.parent_tool_use_id);
      if (group) {
        group.push(entry);
      } else {
        subgroups.set(entry.parent_tool_use_id, [entry]);
      }
    } else {
      mainEntries.push(entry);
    }
  }

  const turns = projectFlatEntries(mainEntries);

  // 先独立投影并折叠每个子组自身的 task 进度，再统一定位锚点：折叠结果
  // 与锚点搜索空间的构建互不依赖，任意处理顺序都收敛到同一结果。
  const subTurnsByParent = new Map<string, Turn[]>();
  for (const [parentId, group] of subgroups) {
    const subTurns = projectFlatEntries(group.map(({ parent_tool_use_id: _omit, ...rest }) => rest));
    resolveStaleTaskBlocks(subTurns);
    foldTaskBlocksIntoAnchors(subTurns);
    subTurnsByParent.set(parentId, subTurns);
  }

  const searchSpace = [turns, ...subTurnsByParent.values()];
  for (const [parentId, subTurns] of subTurnsByParent) {
    const anchor = findAnchorAcross(searchSpace, parentId);
    if (anchor) {
      anchor.sub_turns = subTurns;
    } else {
      // 全局仍无锚点（如懒生成残余组）：以合成锚点独立成卡，不丢子时间线
      turns.push({
        type: "system",
        content: [{ type: "tool_use", id: parentId, name: "Agent", input: {}, sub_turns: subTurns }],
        uuid: `subagent-${parentId}`,
      });
    }
  }

  resolveStaleTaskBlocks(turns);
  foldTaskBlocksIntoAnchors(turns);
  return turns;
}

function findAnchorAcross(turnsList: Turn[][], toolUseId: string): ContentBlock | null {
  for (const turns of turnsList) {
    const found = findToolUseBlock(turns, toolUseId);
    if (found) return found;
  }
  return null;
}

function projectFlatEntries(entries: TimelineEntry[]): Turn[] {
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
      if (entry.subtype === "skill_invocation") {
        // 芯片渲染锚点是 Skill tool_use 块（input 即名与入参）；条目已在
        // 当前 turn 有锚点时不再追加，避免同一调用出现两枚芯片
        const anchored =
          entry.tool_use_id != null &&
          cursor.current !== null &&
          cursor.current.content.some((b) => b.type === "tool_use" && b.id === entry.tool_use_id);
        if (!anchored) {
          attachSystemBlock(entry, {
            type: "skill_invocation",
            skill_name: entry.skill_name ?? undefined,
            skill_args: entry.skill_args ?? undefined,
            tool_use_id: entry.tool_use_id ?? undefined,
          });
        }
        continue;
      }
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

    startTurn({ type: "user", content: blocks, uuid: entry.uuid, timestamp: entry.timestamp });
  }

  flush();
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
  // subagent 的流式草稿不进主时间线：主线只显示折叠卡片，
  // 卡片内容随权威条目（近实时）更新
  if (draft.parent_tool_use_id) return null;
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
