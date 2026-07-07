/**
 * 会话事件日志投影 — entries → Turn[]。
 *
 * 日志条目已在服务端写入点定型（tool_result 独立条目、interrupt / task 通知 /
 * AskUserQuestion 答复为 typed 条目、subagent 条目带 parent_tool_use_id、
 * stream_event 不入日志），本模块只做渲染归组：连续 assistant 条目合并、
 * tool_result 按 tool_use_id 回填、task 按 task_id 就地更新。
 * 不做内容嗅探、不做内容比对去重、不合成消息。
 *
 * 投影分两层：
 * - 内部累积态（fold）：逐条消费条目，只做追加与按 id 回填，永不回溯重放；
 *   条目块在此层做唯一一次深拷贝。
 * - 展示视图（display）：由累积态按 turn 派生——task 折叠、滞留子任务推导
 *   完成、subagent 子时间线挂载都在这层重算；按 turn 版本缓存，只有被新
 *   条目触达的 turn 重建视图，未触达的 turn 保持引用稳定。
 *
 * `projectEntriesToTurns` 是纯函数入口（空投影器全量重放）；
 * `createTimelineProjector` 是同一实现的增量入口，二者结果恒等。
 */

import type {
  ContentBlock,
  DraftDeltaPayload,
  DraftState,
  TimelineEntry,
  Turn,
} from "@/types";

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
// 增量投影器
// ---------------------------------------------------------------------------

/** 内部累积态的 turn：content 为可就地回填的内部块，version 是展示缓存键。 */
interface InternalTurn {
  type: Turn["type"];
  content: ContentBlock[];
  uuid?: string;
  timestamp?: string;
  version: number;
}

/** tool_use 块的登记位置：跨 turn 回填与 subagent 锚定都按此 O(1) 定位。 */
interface ToolUseSite {
  fold: Fold;
  turn: InternalTurn;
  block: ContentBlock;
}

/** 一条平铺时间线（主时间线或某个 subagent 组）的折叠状态。 */
interface Fold {
  committed: InternalTurn[];
  cursor: InternalTurn | null;
  /** 所属 subagent 组（主时间线为 null），脏标记沿锚链向上传播用。 */
  group: SubagentGroup | null;
  version: number;
  displayVersion: number;
  display: Turn[];
}

interface SubagentGroup {
  id: string;
  fold: Fold;
  /** 锚点 tool_use 的位置；null 表示尚未锚定（顶层渲染合成卡片）。 */
  anchor: ToolUseSite | null;
}

export interface TimelineProjector {
  /**
   * 投影 entries → Turn[]，结果与 `projectEntriesToTurns(entries)` 逐值相等。
   * entries 须遵循事件日志契约：按 seq 升序、同 seq 条目不可变、相邻两次
   * 调用之间集合只增不减（append-only 并集）。前缀未变时只折叠新增后缀，
   * 否则整体重放。
   */
  project(entries: TimelineEntry[]): Turn[];
  /** 已增量消费的条目数（缓存规模观测，测试用）。 */
  readonly size: number;
}

export function createTimelineProjector(): TimelineProjector {
  // 前缀指纹：source 为上次 project 的 entries 引用，count 为已折叠条目数
  let source: TimelineEntry[] = [];
  let count = 0;

  let versionCounter = 0;
  let main: Fold = newFold();
  let groups = new Map<string, SubagentGroup>();
  // 尚未锚定的组：compose() 只需扫这个集合而非全部历史 groups，避免长会话
  // 里已锚定完成的 subagent 组随会话增长把每条新消息的合成卡片扫描拖成
  // O(历史 subagent 总数)。
  let pendingGroups = new Set<SubagentGroup>();
  let toolUseSites = new Map<string, ToolUseSite>();
  let turnViewCache = new WeakMap<InternalTurn, { version: number; turn: Turn }>();
  let composed: Turn[] = [];
  let composedDirty = true;

  function newFold(): Fold {
    return { committed: [], cursor: null, group: null, version: 0, displayVersion: -1, display: [] };
  }

  function reset(): void {
    versionCounter = 0;
    main = newFold();
    groups = new Map();
    pendingGroups = new Set();
    toolUseSites = new Map();
    turnViewCache = new WeakMap();
    composed = [];
    composedDirty = true;
    count = 0;
  }

  /**
   * site 所在时间线的锚链（沿既有 anchor 归属向上走）是否会绕回 targetFold。
   * 用于锚定前判定：真实数据里 subagent 嵌套是无环 DAG（parent_tool_use_id
   * 恒指向更早、仍开放的祖先调用），环只可能来自畸形/自指条目
   * （如 parent_tool_use_id 等于自身 tool_use id）。一旦锚定成环，该组的
   * 展示视图会互相依赖、永远无法从主时间线触达，导致整段时间线被
   * compose() 静默丢空——不锚定、退回既有的"合成卡片"兜底比丢空更安全。
   */
  function anchorCreatesCycle(anchorFold: Fold, targetFold: Fold): boolean {
    const seen = new Set<Fold>();
    let current: Fold | null = anchorFold;
    while (current) {
      if (current === targetFold) return true;
      if (seen.has(current)) return false;
      seen.add(current);
      current = current.group?.anchor?.fold ?? null;
    }
    return false;
  }

  /** 条目触达 turn 后：失效其展示缓存，并沿 subagent 锚链逐层失效祖先 turn。 */
  function touch(fold: Fold, turn: InternalTurn): void {
    composedDirty = true;
    turn.version = ++versionCounter;
    const seen = new Set<Fold>();
    let current: Fold | null = fold;
    while (current && !seen.has(current)) {
      seen.add(current);
      current.version = ++versionCounter;
      const anchor: ToolUseSite | null = current.group?.anchor ?? null;
      if (!anchor) break;
      anchor.turn.version = ++versionCounter;
      current = anchor.fold;
    }
  }

  function startTurn(fold: Fold, type: Turn["type"], content: ContentBlock[], entry: TimelineEntry): InternalTurn {
    if (fold.cursor) fold.committed.push(fold.cursor);
    const turn: InternalTurn = { type, content, uuid: entry.uuid, timestamp: entry.timestamp, version: 0 };
    fold.cursor = turn;
    touch(fold, turn);
    return turn;
  }

  /** 追加系统注入块：优先并入当前 assistant turn，否则开 system turn。 */
  function attachSystemBlock(fold: Fold, entry: TimelineEntry, block: ContentBlock): void {
    if (fold.cursor && fold.cursor.type === "assistant") {
      fold.cursor.content.push(block);
      touch(fold, fold.cursor);
      return;
    }
    startTurn(fold, "system", [block], entry);
  }

  function findTaskBlock(content: ContentBlock[], taskId: string): ContentBlock | null {
    for (const block of content) {
      if (block.type === "task_progress" && block.task_id === taskId) return block;
    }
    return null;
  }

  function applyTaskBlock(fold: Fold, entry: TimelineEntry, taskBlock: ContentBlock, updateOnly: boolean): void {
    const taskId = taskBlock.task_id;
    if (taskId && updateOnly && fold.cursor) {
      const existing = findTaskBlock(fold.cursor.content, taskId);
      if (existing) {
        existing.status = taskBlock.status;
        if (taskBlock.summary) existing.summary = taskBlock.summary;
        if (taskBlock.task_status) existing.task_status = taskBlock.task_status;
        if (taskBlock.usage) existing.usage = taskBlock.usage;
        touch(fold, fold.cursor);
        return;
      }
    }
    attachSystemBlock(fold, entry, taskBlock);
  }

  /** tool_result 回填当前 turn 内的发起 tool_use；无匹配时追加独立结果块。 */
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

  /** 登记 tool_use 块位置（同 id 首次登记生效），并锚定等待中的 subagent 组。 */
  function registerToolUse(fold: Fold, turn: InternalTurn, block: ContentBlock): void {
    const id = block.id;
    if (!id) return;
    if (!toolUseSites.has(id)) toolUseSites.set(id, { fold, turn, block });
    const group = groups.get(id);
    if (group && !group.anchor) {
      const site = toolUseSites.get(id);
      if (site && !anchorCreatesCycle(site.fold, group.fold)) {
        group.anchor = site;
        pendingGroups.delete(group);
        // 合成卡片并回锚点：锚点 turn 展示需重建，顶层合成卡片消失
        touch(site.fold, site.turn);
      }
    }
  }

  /** 取 parent_tool_use_id 对应的子时间线折叠状态，组首现时尝试锚定。 */
  function groupFoldFor(parentId: string): Fold {
    let group = groups.get(parentId);
    if (!group) {
      const fold = newFold();
      group = { id: parentId, fold, anchor: null };
      fold.group = group;
      groups.set(parentId, group);
      const site = toolUseSites.get(parentId);
      if (site && !anchorCreatesCycle(site.fold, fold)) {
        group.anchor = site;
        touch(site.fold, site.turn);
      } else {
        pendingGroups.add(group);
      }
      composedDirty = true;
    }
    return group.fold;
  }

  function appendOne(entry: TimelineEntry): void {
    const fold = entry.parent_tool_use_id ? groupFoldFor(entry.parent_tool_use_id) : main;

    if (entry.type === "assistant") {
      const blocks = entryBlocks(entry).map(cloneBlock);
      let turn: InternalTurn;
      if (fold.cursor && fold.cursor.type === "assistant") {
        fold.cursor.content.push(...blocks);
        turn = fold.cursor;
        touch(fold, turn);
      } else {
        turn = startTurn(fold, "assistant", blocks, entry);
      }
      for (const block of blocks) {
        if (block.type === "tool_use") registerToolUse(fold, turn, block);
      }
      return;
    }

    if (entry.type === "tool_result") {
      if (fold.cursor && fold.cursor.type === "assistant") {
        attachToolResult(fold.cursor.content, entry);
        touch(fold, fold.cursor);
      } else {
        attachSystemBlock(fold, entry, {
          type: "tool_result",
          tool_use_id: entry.tool_use_id ?? undefined,
          content: typeof entry.content === "string" ? entry.content : blocksText(entryBlocks(entry)),
          is_error: Boolean(entry.is_error),
        });
      }
      return;
    }

    if (entry.type === "system") {
      if (entry.subtype === "skill_invocation") {
        // 芯片渲染锚点是 Skill tool_use 块（input 即名与入参）；tool_use 块
        // 已登记时不再追加，避免同一调用出现两枚芯片。按 toolUseSites 查找
        // 而非只看 fold.cursor——登记发生在 tool_use 块本身追加时，比
        // cursor 更持久，该 turn 若被 interrupt 等条目提前 flush 出当前
        // turn，仍能正确判定为已锚定（限定同一时间线，与 question_answer
        // 回填口径一致）。
        const site = entry.tool_use_id != null ? toolUseSites.get(entry.tool_use_id) : null;
        const anchored = site != null && site.fold === fold;
        if (!anchored) {
          attachSystemBlock(fold, entry, {
            type: "skill_invocation",
            skill_name: entry.skill_name ?? undefined,
            skill_args: entry.skill_args ?? undefined,
            tool_use_id: entry.tool_use_id ?? undefined,
          });
        }
        return;
      }
      if (entry.subtype === "interrupt") {
        startTurn(fold, "system", [{ type: "interrupt_notice" }], entry);
        return;
      }
      if (entry.subtype !== "task_started" && entry.subtype !== "task_progress" && entry.subtype !== "task_notification") {
        return;
      }
      applyTaskBlock(
        fold,
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
      return;
    }

    // entry.type === "user"
    if (entry.subtype === "question_answer") {
      const resultText = typeof entry.content === "string" ? entry.content : blocksText(entryBlocks(entry));
      const answers = entry.answers ?? undefined;
      // 回填提问的 AskUserQuestion tool_use 块：结果状态不悬挂，问题卡可标记所选。
      // 按登记索引 O(1) 定位，限定同一时间线（pending 提问期间若有 interrupt 之类
      // 的条目提前把该 turn flush 出去，锚点仍要能跨 turn 找回）。
      let site: ToolUseSite | null = null;
      if (entry.tool_use_id) {
        const hit = toolUseSites.get(entry.tool_use_id);
        if (hit && hit.fold === fold) site = hit;
      }
      if (site) {
        site.block.result = resultText;
        site.block.is_error = Boolean(entry.is_error);
        if (answers) site.block.answers = { ...answers };
        touch(site.fold, site.turn);
      }
      if (entry.is_error) {
        // 被拒/中断的提问没有用户答复；无处回填时保留孤立结果块
        if (!site) {
          attachSystemBlock(fold, entry, {
            type: "tool_result",
            tool_use_id: entry.tool_use_id ?? undefined,
            content: resultText,
            is_error: true,
          });
        }
        return;
      }
      startTurn(fold, "user", [{ type: "question_answer", answers, text: resultText }], entry);
      return;
    }

    startTurn(fold, "user", entryBlocks(entry).map(cloneBlock), entry);
  }

  // -------------------------------------------------------------------------
  // 展示视图 — 由累积态派生，按 turn 版本缓存
  // -------------------------------------------------------------------------

  function foldDisplay(fold: Fold): Turn[] {
    if (fold.displayVersion === fold.version) return fold.display;
    const internal = fold.cursor ? [...fold.committed, fold.cursor] : [...fold.committed];
    fold.display = internal.map(turnView);
    fold.displayVersion = fold.version;
    return fold.display;
  }

  function turnView(turn: InternalTurn): Turn {
    const cached = turnViewCache.get(turn);
    if (cached && cached.version === turn.version) return cached.turn;
    const built = buildTurnView(turn);
    turnViewCache.set(turn, { version: turn.version, turn: built });
    return built;
  }

  /**
   * 构建单个 turn 的展示视图：挂载 subagent 子时间线、把已完成 Agent 调用
   * 的滞留 task_started 推导为完成、将 task 进度折叠进锚点 tool_use（子任务
   * 卡片就地显示状态与进度，不渲染独立进度行；无锚点的 task 块保持原样）。
   * 可变块（tool_use / task_progress）逐次浅拷，累积态不被展示层污染，
   * 同一版本重复构建收敛到同一结果。
   */
  function buildTurnView(turn: InternalTurn): Turn {
    const content: ContentBlock[] = [];
    let toolUseById: Map<string, ContentBlock> | null = null;
    let completedAgentIds: Set<string> | null = null;
    for (const block of turn.content) {
      if (block.type === "tool_use") {
        const view = { ...block };
        if (block.id) {
          const group = groups.get(block.id);
          if (group && group.anchor?.block === block) view.sub_turns = foldDisplay(group.fold);
          (toolUseById ??= new Map()).set(block.id, view);
          if (view.name === "Agent" && view.result !== undefined) (completedAgentIds ??= new Set()).add(block.id);
        }
        content.push(view);
      } else if (block.type === "task_progress") {
        content.push({ ...block });
      } else {
        content.push(block);
      }
    }
    // task_started 块对应的 Agent tool_use 已有 result 时推导为已完成
    if (completedAgentIds) {
      const completed = completedAgentIds;
      for (const block of content) {
        if (
          block.type === "task_progress" &&
          block.status === "task_started" &&
          block.tool_use_id &&
          completed.has(block.tool_use_id)
        ) {
          block.status = "task_notification";
          block.task_status = "completed";
        }
      }
    }
    let finalContent = content;
    if (toolUseById) {
      const anchors = toolUseById;
      finalContent = content.filter((block) => {
        if (block.type !== "task_progress" || !block.tool_use_id) return true;
        const anchor = anchors.get(block.tool_use_id);
        if (!anchor) return true;
        anchor.task_info = block;
        return false;
      });
    }
    return { type: turn.type, content: finalContent, uuid: turn.uuid, timestamp: turn.timestamp };
  }

  function compose(): Turn[] {
    if (!composedDirty) return composed;
    const out = [...foldDisplay(main)];
    // 仍无锚点的组（如懒生成残余组、成环被拒锚的组）：以合成锚点独立成卡，
    // 不丢子时间线。只扫 pendingGroups（当前仍未锚定的组），不扫全部历史
    // groups——已锚定的组不会再变回待锚定，扫描量不随会话内 subagent 总数增长。
    for (const group of pendingGroups) {
      out.push({
        type: "system",
        content: [{ type: "tool_use", id: group.id, name: "Agent", input: {}, sub_turns: foldDisplay(group.fold) }],
        uuid: `subagent-${group.id}`,
      });
    }
    composed = out;
    composedDirty = false;
    return out;
  }

  return {
    project(entries: TimelineEntry[]): Turn[] {
      // 前缀不变性按端点引用判定：日志契约保证条目按 seq 不可变、集合只增
      // 不减，端点同引用即前缀逐元素同引用（中部插入必然移动端点索引）。
      const extendsPrefix =
        entries.length >= count &&
        (count === 0 || (entries[0] === source[0] && entries[count - 1] === source[count - 1]));
      if (!extendsPrefix) reset();
      for (let i = count; i < entries.length; i++) appendOne(entries[i]);
      source = entries;
      count = entries.length;
      return compose();
    },
    get size(): number {
      return count;
    },
  };
}

/**
 * 全量投影纯函数入口（entries → Turn[]）：空投影器一次性重放。
 * 增量路径 `createTimelineProjector` 与本函数结果恒等。
 */
export function projectEntriesToTurns(entries: TimelineEntry[]): Turn[] {
  return createTimelineProjector().project(entries);
}

// ---------------------------------------------------------------------------
// draft 投影与增量应用
// ---------------------------------------------------------------------------

/** 收集已落权威 assistant 条目的 message_id 集合（draft 替换判定的派生索引）。 */
export function collectCommittedMessageIds(entries: TimelineEntry[]): Set<string> {
  const ids = new Set<string>();
  for (const entry of entries) {
    if (entry.type === "assistant" && entry.message_id != null) ids.add(entry.message_id);
  }
  return ids;
}

/**
 * draft 是否已被权威条目替换：按身份比对（同 message_id 的 assistant 条目
 * 已落库即视为替换），不做内容比对。集合由调用方增量维护，判定 O(1)。
 */
export function isDraftReplaced(
  draft: DraftState | null,
  committedMessageIds: ReadonlySet<string>,
): boolean {
  if (!draft || !draft.message_id) return false;
  return committedMessageIds.has(draft.message_id);
}

/**
 * draft → Turn。replaced 为真（已被权威条目替换）时返回 null；
 * subagent 的流式草稿不进主时间线：主线只显示折叠卡片，
 * 卡片内容随权威条目（近实时）更新。
 */
export function buildDraftTurn(draft: DraftState | null, replaced: boolean): Turn | null {
  if (!draft || !draft.message_id) return null;
  if (draft.parent_tool_use_id) return null;
  if (replaced) return null;
  const content = (draft.content ?? []).filter(Boolean);
  if (content.length === 0) return null;
  return {
    type: "assistant",
    content,
    uuid: `draft-${draft.message_id}`,
  };
}

/** draft → Turn 全量入口：从 entries 派生替换判定（热路径请用 buildDraftTurn）。 */
export function projectDraftToTurn(
  draft: DraftState | null,
  entries: TimelineEntry[],
): Turn | null {
  return buildDraftTurn(draft, isDraftReplaced(draft, collectCommittedMessageIds(entries)));
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
