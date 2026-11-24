
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


interface InternalTurn {
  type: Turn["type"];
  content: ContentBlock[];
  uuid?: string;
  timestamp?: string;
  version: number;
}

interface ToolUseSite {
  fold: Fold;
  turn: InternalTurn;
  block: ContentBlock;
}

interface Fold {
  committed: InternalTurn[];
  cursor: InternalTurn | null;
  group: SubagentGroup | null;
  version: number;
  displayVersion: number;
  display: Turn[];
}

interface SubagentGroup {
  id: string;
  fold: Fold;
  anchor: ToolUseSite | null;
}

export interface TimelineProjector {
  project(entries: TimelineEntry[]): Turn[];
  readonly size: number;
}

export function createTimelineProjector(): TimelineProjector {
  let source: TimelineEntry[] = [];
  let count = 0;

  let versionCounter = 0;
  let main: Fold = newFold();
  let groups = new Map<string, SubagentGroup>();
  let pendingGroups = new Set<SubagentGroup>();
  let subagentOutcomes = new Map<string, ContentBlock>();
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
    subagentOutcomes = new Map();
    toolUseSites = new Map();
    turnViewCache = new WeakMap();
    composed = [];
    composedDirty = true;
    count = 0;
  }

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

  function findTaskSite(fold: Fold, taskId: string): { turn: InternalTurn; block: ContentBlock } | null {
    if (fold.cursor) {
      const found = findTaskBlock(fold.cursor.content, taskId);
      if (found) return { turn: fold.cursor, block: found };
    }
    for (let i = fold.committed.length - 1; i >= 0; i -= 1) {
      const turn = fold.committed[i];
      const found = findTaskBlock(turn.content, taskId);
      if (found) return { turn, block: found };
    }
    return null;
  }

  function applyTaskBlock(fold: Fold, entry: TimelineEntry, taskBlock: ContentBlock, updateOnly: boolean): void {
    const taskId = taskBlock.task_id;
    if (taskId && updateOnly) {
      const site = findTaskSite(fold, taskId);
      if (site) {
        const existing = site.block;
        existing.status = taskBlock.status;
        if (taskBlock.summary) existing.summary = taskBlock.summary;
        if (taskBlock.task_status) existing.task_status = taskBlock.task_status;
        if (taskBlock.usage) existing.usage = taskBlock.usage;
        touch(fold, site.turn);
        return;
      }
    }
    attachSystemBlock(fold, entry, taskBlock);
  }

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
        touch(site.fold, site.turn);
      }
    }
  }

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
      const resultText = typeof entry.content === "string" ? entry.content : blocksText(entryBlocks(entry));
      let site: ToolUseSite | null = null;
      if (entry.tool_use_id) {
        const hit = toolUseSites.get(entry.tool_use_id);
        if (hit && hit.fold === fold) site = hit;
      }
      if (site) {
        site.block.result = resultText;
        site.block.is_error = Boolean(entry.is_error);
        touch(site.fold, site.turn);
      } else {
        attachSystemBlock(fold, entry, {
          type: "tool_result",
          tool_use_id: entry.tool_use_id ?? undefined,
          content: resultText,
          is_error: Boolean(entry.is_error),
        });
      }
      return;
    }

    if (entry.type === "system") {
      if (entry.subtype === "agent_turn_failure" && entry.failure) {
        startTurn(
          fold,
          "system",
          [{ type: "agent_failure", failure: structuredClone(entry.failure) }],
          entry,
        );
        return;
      }
      if (entry.subtype === "skill_invocation") {
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
      if (entry.subtype === "subagent_outcome") {
        if (entry.tool_use_id) {
          subagentOutcomes.set(entry.tool_use_id, {
            type: "task_progress",
            status: "task_notification",
            description: entry.description ?? "",
            summary: entry.summary ?? undefined,
            task_status: entry.task_status ?? undefined,
            tool_use_id: entry.tool_use_id,
          });
          composedDirty = true;
        }
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

    if (entry.subtype === "compact_summary") {
      startTurn(fold, "system", [{ type: "compact_summary", text: blocksText(entryBlocks(entry)) }], entry);
      return;
    }
    if (entry.subtype === "question_answer") {
      const resultText = typeof entry.content === "string" ? entry.content : blocksText(entryBlocks(entry));
      const answers = entry.answers ?? undefined;
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
    for (const group of pendingGroups) {
      const card: ContentBlock = { type: "tool_use", id: group.id, name: "Agent", input: {}, sub_turns: foldDisplay(group.fold) };
      const outcome = subagentOutcomes.get(group.id);
      if (outcome) card.task_info = { ...outcome };
      out.push({ type: "system", content: [card], uuid: `subagent-${group.id}` });
    }
    composed = out;
    composedDirty = false;
    return out;
  }

  return {
    project(entries: TimelineEntry[]): Turn[] {
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

export function projectEntriesToTurns(entries: TimelineEntry[]): Turn[] {
  return createTimelineProjector().project(entries);
}


export function collectCommittedMessageIds(entries: TimelineEntry[]): Set<string> {
  const ids = new Set<string>();
  for (const entry of entries) {
    if (entry.type === "assistant" && entry.message_id != null) ids.add(entry.message_id);
  }
  return ids;
}

export function isDraftReplaced(
  draft: DraftState | null,
  committedMessageIds: ReadonlySet<string>,
): boolean {
  if (!draft || !draft.message_id) return false;
  return committedMessageIds.has(draft.message_id);
}

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

export function projectDraftToTurn(
  draft: DraftState | null,
  entries: TimelineEntry[],
): Turn | null {
  return buildDraftTurn(draft, isDraftReplaced(draft, collectCommittedMessageIds(entries)));
}

export interface DraftMirror extends DraftState {
  toolJson: Record<number, string>;
}

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
