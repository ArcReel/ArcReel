import { describe, expect, it } from "vitest";
import type { DraftDeltaPayload, DraftState, TimelineEntry } from "@/types";
import {
  applyDraftDelta,
  buildDraftTurn,
  collectCommittedMessageIds,
  createTimelineProjector,
  isDraftReplaced,
  mergeEntriesBySeq,
  projectDraftToTurn,
  projectEntriesToTurns,
  type DraftMirror,
} from "./entry-projection";

let seqCounter = 0;

function entry(partial: Omit<TimelineEntry, "seq">): TimelineEntry {
  return { seq: seqCounter++, ...partial };
}

function userEntry(text: string, extra: Partial<TimelineEntry> = {}): TimelineEntry {
  return entry({ type: "user", content: [{ type: "text", text }], uuid: `u-${seqCounter}`, ...extra });
}

describe("projectEntriesToTurns", () => {
  it("projects user and assistant entries into separate turns", () => {
    const turns = projectEntriesToTurns([
      userEntry("你好"),
      entry({ type: "assistant", content: [{ type: "text", text: "你好！" }], uuid: "a-1" }),
    ]);
    expect(turns.map((t) => t.type)).toEqual(["user", "assistant"]);
    expect(turns[0].content[0].text).toBe("你好");
    expect(turns[1].content[0].text).toBe("你好！");
  });

  it("merges consecutive assistant entries into one turn", () => {
    const turns = projectEntriesToTurns([
      entry({ type: "assistant", content: [{ type: "text", text: "第一段" }], uuid: "a-1" }),
      entry({ type: "assistant", content: [{ type: "text", text: "第二段" }], uuid: "a-2" }),
    ]);
    expect(turns).toHaveLength(1);
    expect(turns[0].content.map((b) => b.text)).toEqual(["第一段", "第二段"]);
  });

  it("backfills tool_result into matching tool_use without breaking the assistant merge", () => {
    const turns = projectEntriesToTurns([
      entry({
        type: "assistant",
        content: [{ type: "tool_use", id: "tu-1", name: "Read", input: {} }],
        uuid: "a-1",
      }),
      entry({ type: "tool_result", tool_use_id: "tu-1", content: "文件内容", is_error: false, uuid: "tr-1" }),
      entry({ type: "assistant", content: [{ type: "text", text: "读完了" }], uuid: "a-2" }),
    ]);
    expect(turns).toHaveLength(1);
    const toolUse = turns[0].content[0];
    expect(toolUse.result).toBe("文件内容");
    expect(toolUse.is_error).toBe(false);
    expect(turns[0].content[1].text).toBe("读完了");
  });

  it("appends unmatched tool_result as a standalone block", () => {
    const turns = projectEntriesToTurns([
      entry({ type: "assistant", content: [{ type: "text", text: "hi" }], uuid: "a-1" }),
      entry({ type: "tool_result", tool_use_id: "tu-x", content: "孤儿结果", is_error: true, uuid: "tr-1" }),
    ]);
    expect(turns).toHaveLength(1);
    const block = turns[0].content[1];
    expect(block.type).toBe("tool_result");
    expect(block.content).toBe("孤儿结果");
    expect(block.is_error).toBe(true);
  });

  it("renders typed interrupt entries as interrupt_notice system turns", () => {
    const turns = projectEntriesToTurns([
      userEntry("做点什么"),
      entry({ type: "system", subtype: "interrupt", uuid: "i-1", timestamp: "2026-01-01T00:00:00Z" }),
    ]);
    expect(turns.map((t) => t.type)).toEqual(["user", "system"]);
    expect(turns[1].content).toEqual([{ type: "interrupt_notice" }]);
    expect(turns[1].uuid).toBe("i-1");
  });

  it("does not sniff interrupt echo text in user entries (typing happens at the write point)", () => {
    const turns = projectEntriesToTurns([userEntry("[Request interrupted by user]")]);
    expect(turns.map((t) => t.type)).toEqual(["user"]);
    expect(turns[0].content[0].type).toBe("text");
  });

  it("maps system task entries to task_progress blocks and updates by task_id", () => {
    const turns = projectEntriesToTurns([
      entry({ type: "assistant", content: [{ type: "text", text: "启动子任务" }], uuid: "a-1" }),
      entry({ type: "system", subtype: "task_started", task_id: "t1", description: "分析", uuid: "s-1" }),
      entry({
        type: "system",
        subtype: "task_notification",
        task_id: "t1",
        summary: "完成",
        task_status: "completed",
        uuid: "s-2",
      }),
    ]);
    expect(turns).toHaveLength(1);
    const taskBlocks = turns[0].content.filter((b) => b.type === "task_progress");
    expect(taskBlocks).toHaveLength(1);
    expect(taskBlocks[0].status).toBe("task_notification");
    expect(taskBlocks[0].summary).toBe("完成");
    expect(taskBlocks[0].task_status).toBe("completed");
  });

  it("updates the existing task block in place from a typed task_notification entry (no duplicate bubble)", () => {
    // 写入点已把 task 通知 XML 定型为 system 条目——SDK 双通道（typed 系统
    // 消息 + 注入用户消息）产生的两条 typed 条目按 task_id 就地合并。
    const turns = projectEntriesToTurns([
      entry({ type: "assistant", content: [{ type: "text", text: "spawn" }], uuid: "a-1" }),
      entry({ type: "system", subtype: "task_started", task_id: "t9", description: "d", uuid: "s-1" }),
      entry({
        type: "system",
        subtype: "task_notification",
        task_id: "t9",
        summary: "子任务完成",
        task_status: "completed",
        tool_use_id: "tu-9",
        uuid: "s-2",
      }),
      entry({
        type: "system",
        subtype: "task_notification",
        task_id: "t9",
        summary: "子任务完成",
        task_status: "completed",
        tool_use_id: "tu-9",
        uuid: "n-1",
      }),
    ]);
    expect(turns).toHaveLength(1);
    const taskBlocks = turns[0].content.filter((b) => b.type === "task_progress");
    expect(taskBlocks).toHaveLength(1);
    expect(taskBlocks[0].summary).toBe("子任务完成");
    expect(taskBlocks[0].task_status).toBe("completed");
  });

  it("does not sniff task-notification XML in user entries (typing happens at the write point)", () => {
    const xml =
      "<task-notification><task-id>t9</task-id><tool-use-id>tu-9</tool-use-id>" +
      "<status>completed</status><summary>done</summary></task-notification>";
    const turns = projectEntriesToTurns([userEntry(xml)]);
    expect(turns.map((t) => t.type)).toEqual(["user"]);
  });

  it("backfills question_answer into the AskUserQuestion tool_use and emits a user answer turn", () => {
    const turns = projectEntriesToTurns([
      entry({
        type: "assistant",
        content: [{ type: "tool_use", id: "tu-q", name: "AskUserQuestion", input: { questions: [] } }],
        uuid: "a-1",
      }),
      entry({
        type: "user",
        subtype: "question_answer",
        tool_use_id: "tu-q",
        content: 'Your questions have been answered: "继续吗?"="继续".',
        is_error: false,
        answers: { "继续吗?": "继续" },
        uuid: "qa-1",
      }),
      entry({ type: "assistant", content: [{ type: "text", text: "好的，继续" }], uuid: "a-2" }),
    ]);
    expect(turns.map((t) => t.type)).toEqual(["assistant", "user", "assistant"]);
    // 提问的 tool_use 块获得结果回填（状态不再悬挂）
    const toolUse = turns[0].content[0];
    expect(toolUse.result).toContain("answered");
    expect(toolUse.is_error).toBe(false);
    expect(toolUse.answers).toEqual({ "继续吗?": "继续" });
    // 答复自成用户侧条目
    expect(turns[1].content[0].type).toBe("question_answer");
    expect(turns[1].content[0].answers).toEqual({ "继续吗?": "继续" });
  });

  it("question_answer without structured answers still emits an answer turn with raw text", () => {
    const turns = projectEntriesToTurns([
      entry({
        type: "assistant",
        content: [{ type: "tool_use", id: "tu-q", name: "AskUserQuestion", input: {} }],
        uuid: "a-1",
      }),
      entry({
        type: "user",
        subtype: "question_answer",
        tool_use_id: "tu-q",
        content: "answered",
        is_error: false,
        uuid: "qa-1",
      }),
    ]);
    expect(turns.map((t) => t.type)).toEqual(["assistant", "user"]);
    expect(turns[1].content[0].type).toBe("question_answer");
    expect(turns[1].content[0].text).toBe("answered");
  });

  it("denied question_answer backfills the tool_use as error without an answer turn", () => {
    const turns = projectEntriesToTurns([
      entry({
        type: "assistant",
        content: [{ type: "tool_use", id: "tu-q", name: "AskUserQuestion", input: {} }],
        uuid: "a-1",
      }),
      entry({
        type: "user",
        subtype: "question_answer",
        tool_use_id: "tu-q",
        content: "session interrupted by user",
        is_error: true,
        uuid: "qa-1",
      }),
    ]);
    expect(turns).toHaveLength(1);
    expect(turns[0].content[0].is_error).toBe(true);
  });

  it("backfills question_answer onto the AskUserQuestion tool_use even when an interrupt entry flushed the turn first", () => {
    const turns = projectEntriesToTurns([
      entry({
        type: "assistant",
        content: [{ type: "tool_use", id: "tu-q", name: "AskUserQuestion", input: { questions: [] } }],
        uuid: "a-1",
      }),
      entry({ type: "system", subtype: "interrupt", uuid: "i-1" }),
      entry({
        type: "user",
        subtype: "question_answer",
        tool_use_id: "tu-q",
        content: 'Your questions have been answered: "继续吗?"="继续".',
        is_error: false,
        answers: { "继续吗?": "继续" },
        uuid: "qa-1",
      }),
    ]);
    expect(turns.map((t) => t.type)).toEqual(["assistant", "system", "user"]);
    // 锚点 tool_use 已被 interrupt 提前 flush 到前一个 turn，回填仍要跨 turn 找到它
    const toolUse = turns[0].content[0];
    expect(toolUse.result).toContain("answered");
    expect(toolUse.is_error).toBe(false);
    expect(toolUse.answers).toEqual({ "继续吗?": "继续" });
    expect(turns[2].content[0].type).toBe("question_answer");
  });

  it("skips skill_invocation entries anchored to a Skill tool_use in the current turn", () => {
    const turns = projectEntriesToTurns([
      entry({
        type: "assistant",
        content: [{ type: "tool_use", id: "tu-s", name: "Skill", input: { skill: "manage-project", args: "x" } }],
        uuid: "a-1",
      }),
      entry({
        type: "system",
        subtype: "skill_invocation",
        skill_name: "manage-project",
        skill_args: "x",
        tool_use_id: "tu-s",
        uuid: "s-1",
      }),
    ]);
    expect(turns).toHaveLength(1);
    // 芯片渲染锚点是 Skill tool_use 块本身，条目不产生第二个块
    expect(turns[0].content).toHaveLength(1);
    expect(turns[0].content[0].type).toBe("tool_use");
  });

  it("still recognizes the Skill tool_use anchor after it's flushed to an earlier turn by an interrupt", () => {
    // anchored 判定若只看 fold.cursor，Skill 的 tool_use 块所在 turn 一旦被
    // interrupt 等条目提前 flush 出当前 turn，后到的 skill_invocation 就会
    // 误判为"未锚定"，重复渲染一个独立芯片。toolUseSites 登记在 tool_use
    // 块本身追加时发生，比 cursor 更持久，应据此判定而非只看当前 turn。
    const turns = projectEntriesToTurns([
      entry({
        type: "assistant",
        content: [{ type: "tool_use", id: "tu-s", name: "Skill", input: { skill: "manage-project", args: "x" } }],
        uuid: "a-1",
      }),
      entry({ type: "system", subtype: "interrupt", uuid: "i-1" }),
      entry({
        type: "system",
        subtype: "skill_invocation",
        skill_name: "manage-project",
        skill_args: "x",
        tool_use_id: "tu-s",
        uuid: "s-1",
      }),
    ]);
    expect(turns).toHaveLength(2);
    expect(turns.some((t) => t.content.some((b) => b.type === "skill_invocation"))).toBe(false);
  });

  it("renders unanchored skill_invocation entries as standalone chip blocks", () => {
    const turns = projectEntriesToTurns([
      entry({
        type: "system",
        subtype: "skill_invocation",
        skill_name: "commit",
        skill_args: null,
        tool_use_id: null,
        uuid: "s-1",
      }),
    ]);
    expect(turns).toHaveLength(1);
    expect(turns[0].type).toBe("system");
    expect(turns[0].content[0]).toMatchObject({ type: "skill_invocation", skill_name: "commit" });
  });

  it("groups subagent entries into sub_turns on the anchoring tool_use instead of the main timeline", () => {
    const turns = projectEntriesToTurns([
      entry({
        type: "assistant",
        content: [{ type: "tool_use", id: "tu-agent", name: "Agent", input: { description: "探索" } }],
        uuid: "a-1",
      }),
      userEntry("subagent 内部 prompt", { parent_tool_use_id: "tu-agent" }),
      entry({
        type: "assistant",
        content: [{ type: "text", text: "subagent 回复" }],
        uuid: "a-2",
        parent_tool_use_id: "tu-agent",
      }),
      entry({ type: "tool_result", tool_use_id: "tu-agent", content: "报告", is_error: false, uuid: "tr-1" }),
      entry({ type: "assistant", content: [{ type: "text", text: "主线总结" }], uuid: "a-3" }),
    ]);
    // 主时间线：单一 assistant turn（卡片锚点 + 主线文本），无平铺的 subagent 消息
    expect(turns).toHaveLength(1);
    const [anchor, summary] = turns[0].content;
    expect(anchor.type).toBe("tool_use");
    expect(anchor.result).toBe("报告");
    expect(summary.text).toBe("主线总结");
    // 子时间线完整、内部按序
    expect(anchor.sub_turns?.map((t) => t.type)).toEqual(["user", "assistant"]);
    expect(anchor.sub_turns?.[1].content[0].text).toBe("subagent 回复");
  });

  it("renders unanchored subagent groups as a synthetic standalone card", () => {
    const turns = projectEntriesToTurns([
      userEntry("主线消息"),
      entry({
        type: "assistant",
        content: [{ type: "text", text: "孤儿子时间线" }],
        uuid: "a-1",
        parent_tool_use_id: "tu-ghost",
      }),
    ]);
    expect(turns).toHaveLength(2);
    const card = turns[1].content[0];
    expect(card.type).toBe("tool_use");
    expect(card.id).toBe("tu-ghost");
    expect(card.sub_turns?.[0].content[0].text).toBe("孤儿子时间线");
  });

  it("anchors a nested subagent (subagent launching its own subagent) inside the outer sub-timeline, not as a duplicate top-level card", () => {
    const turns = projectEntriesToTurns([
      entry({
        type: "assistant",
        content: [{ type: "tool_use", id: "tu-outer", name: "Agent", input: { description: "外层任务" } }],
        uuid: "a-1",
      }),
      entry({
        type: "assistant",
        content: [{ type: "tool_use", id: "tu-inner", name: "Agent", input: { description: "内层任务" } }],
        uuid: "a-2",
        parent_tool_use_id: "tu-outer",
      }),
      entry({
        type: "assistant",
        content: [{ type: "text", text: "内层子任务回复" }],
        uuid: "a-3",
        parent_tool_use_id: "tu-inner",
      }),
    ]);
    // 主时间线只有外层卡片，内层锚点在外层的子时间线内部，不重复出现在顶层
    expect(turns).toHaveLength(1);
    const outerAnchor = turns[0].content[0];
    expect(outerAnchor.id).toBe("tu-outer");
    expect(outerAnchor.sub_turns).toHaveLength(1);
    const innerAnchor = outerAnchor.sub_turns?.[0].content[0];
    expect(innerAnchor?.type).toBe("tool_use");
    expect(innerAnchor?.id).toBe("tu-inner");
    expect(innerAnchor?.sub_turns?.[0].content[0].text).toBe("内层子任务回复");
  });

  it("folds task_progress blocks scoped inside a subagent's own sub-timeline into its nested anchor", () => {
    const turns = projectEntriesToTurns([
      entry({
        type: "assistant",
        content: [{ type: "tool_use", id: "tu-agent", name: "Agent", input: { description: "外层任务" } }],
        uuid: "a-1",
      }),
      entry({
        type: "assistant",
        content: [{ type: "tool_use", id: "tu-nested", name: "Agent", input: { description: "内层任务" } }],
        uuid: "a-2",
        parent_tool_use_id: "tu-agent",
      }),
      entry({
        type: "system",
        subtype: "task_started",
        task_id: "t1",
        tool_use_id: "tu-nested",
        description: "内层任务",
        uuid: "s-1",
        parent_tool_use_id: "tu-agent",
      }),
      entry({
        type: "system",
        subtype: "task_progress",
        task_id: "t1",
        tool_use_id: "tu-nested",
        usage: { total_tokens: 7 },
        uuid: "s-2",
        parent_tool_use_id: "tu-agent",
      }),
    ]);
    const outerAnchor = turns[0].content[0];
    const subTimeline = outerAnchor.sub_turns?.[0];
    // 子时间线内部的进度同样折叠进锚点，不留下独立 task_progress 行
    expect(subTimeline?.content.filter((b) => b.type === "task_progress")).toHaveLength(0);
    const nestedAnchor = subTimeline?.content[0];
    expect(nestedAnchor?.task_info?.usage?.total_tokens).toBe(7);
  });

  it("folds task_progress blocks into the anchoring tool_use as task_info", () => {
    const turns = projectEntriesToTurns([
      entry({
        type: "assistant",
        content: [{ type: "tool_use", id: "tu-a", name: "Agent", input: { description: "分析" } }],
        uuid: "a-1",
      }),
      entry({
        type: "system",
        subtype: "task_started",
        task_id: "t1",
        tool_use_id: "tu-a",
        description: "分析",
        uuid: "s-1",
      }),
      entry({
        type: "system",
        subtype: "task_progress",
        task_id: "t1",
        tool_use_id: "tu-a",
        usage: { total_tokens: 42 },
        uuid: "s-2",
      }),
    ]);
    expect(turns).toHaveLength(1);
    // 进度不再渲染独立行，折叠进卡片锚点
    expect(turns[0].content.filter((b) => b.type === "task_progress")).toHaveLength(0);
    const anchor = turns[0].content[0];
    expect(anchor.task_info?.status).toBe("task_progress");
    expect(anchor.task_info?.usage?.total_tokens).toBe(42);
  });

  it("auto-completes stale task_started blocks whose Agent tool_use has a result", () => {
    const turns = projectEntriesToTurns([
      entry({
        type: "assistant",
        content: [{ type: "tool_use", id: "tu-a", name: "Agent", input: {} }],
        uuid: "a-1",
      }),
      entry({ type: "system", subtype: "task_started", task_id: "t2", tool_use_id: "tu-a", uuid: "s-1" }),
      entry({ type: "tool_result", tool_use_id: "tu-a", content: "done", is_error: false, uuid: "tr-1" }),
    ]);
    const anchor = turns[0].content.find((b) => b.type === "tool_use");
    expect(anchor?.task_info?.status).toBe("task_notification");
    expect(anchor?.task_info?.task_status).toBe("completed");
  });

  it("does not mutate input entries", () => {
    const source = [
      entry({
        type: "assistant" as const,
        content: [{ type: "tool_use" as const, id: "tu-1", name: "Read", input: {} }],
        uuid: "a-1",
      }),
      entry({ type: "tool_result" as const, tool_use_id: "tu-1", content: "r", is_error: false, uuid: "tr-1" }),
    ];
    const copy = JSON.parse(JSON.stringify(source)) as TimelineEntry[];
    projectEntriesToTurns(source);
    expect(source).toEqual(copy);
  });
});

describe("projectDraftToTurn", () => {
  const draft: DraftMirror = {
    message_id: "msg_1",
    parent_tool_use_id: null,
    content: [{ type: "text", text: "流式中" }],
    toolJson: {},
  };

  it("returns an assistant turn for an active draft", () => {
    const turn = projectDraftToTurn(draft, []);
    expect(turn?.type).toBe("assistant");
    expect(turn?.uuid).toBe("draft-msg_1");
    expect(turn?.content[0].text).toBe("流式中");
  });

  it("returns null when a same-message_id authoritative entry exists (identity match, not content compare)", () => {
    const entries: TimelineEntry[] = [
      { seq: 0, type: "assistant", message_id: "msg_1", content: [{ type: "text", text: "完全不同的内容" }] },
    ];
    expect(projectDraftToTurn(draft, entries)).toBeNull();
  });

  it("returns null for null or empty drafts", () => {
    expect(projectDraftToTurn(null, [])).toBeNull();
    expect(projectDraftToTurn({ ...draft, content: [] }, [])).toBeNull();
  });

  it("returns null for subagent drafts (main timeline shows only the collapsed card)", () => {
    expect(projectDraftToTurn({ ...draft, parent_tool_use_id: "tu-agent" }, [])).toBeNull();
  });
});

describe("applyDraftDelta", () => {
  function delta(partial: Partial<DraftDeltaPayload>): DraftDeltaPayload {
    return {
      message_id: "msg_1",
      delta_type: "text_delta",
      block_index: 0,
      rev: 1,
      ...partial,
    };
  }

  it("accumulates text deltas by block index", () => {
    let draft = applyDraftDelta(null, delta({ text: "你" }));
    draft = applyDraftDelta(draft, delta({ text: "好", rev: 2 }));
    expect(draft.content[0]).toEqual({ type: "text", text: "你好" });
  });

  it("starts a fresh draft when message_id changes", () => {
    const first = applyDraftDelta(null, delta({ text: "旧" }));
    const second = applyDraftDelta(first, delta({ message_id: "msg_2", text: "新", rev: 2 }));
    expect(second.message_id).toBe("msg_2");
    expect(second.content[0].text).toBe("新");
  });

  it("applies block_start with the provided block", () => {
    const draft = applyDraftDelta(
      null,
      delta({ delta_type: "block_start", block: { type: "tool_use", id: "tu-1", name: "Read", input: {} } }),
    );
    expect(draft.content[0].type).toBe("tool_use");
    expect(draft.content[0].name).toBe("Read");
  });

  it("accumulates thinking deltas", () => {
    let draft = applyDraftDelta(null, delta({ delta_type: "thinking_delta", thinking: "思" }));
    draft = applyDraftDelta(draft, delta({ delta_type: "thinking_delta", thinking: "考", rev: 2 }));
    expect(draft.content[0]).toEqual({ type: "thinking", thinking: "思考" });
  });

  it("accumulates partial JSON and parses once complete", () => {
    let draft = applyDraftDelta(null, delta({ delta_type: "input_json_delta", partial_json: '{"path": "a' }));
    expect(draft.content[0].input).toEqual({});
    draft = applyDraftDelta(draft, delta({ delta_type: "input_json_delta", partial_json: '.txt"}', rev: 2 }));
    expect(draft.content[0].input).toEqual({ path: "a.txt" });
  });

  it("does not mutate the previous draft object", () => {
    const first = applyDraftDelta(null, delta({ text: "你" }));
    const before = JSON.parse(JSON.stringify(first)) as DraftMirror;
    applyDraftDelta(first, delta({ text: "好", rev: 2 }));
    expect(first).toEqual(before);
  });
});

describe("collectCommittedMessageIds", () => {
  it("collects message_id of assistant entries only", () => {
    const ids = collectCommittedMessageIds([
      { seq: 0, type: "user", content: [{ type: "text", text: "hi" }] },
      { seq: 1, type: "assistant", message_id: "m1", content: [{ type: "text", text: "a" }] },
      { seq: 2, type: "assistant", message_id: "m2", content: [{ type: "text", text: "b" }] },
      { seq: 3, type: "tool_result", tool_use_id: "tu", content: "r" },
      // assistant 条目缺 message_id 时不计入
      { seq: 4, type: "assistant", content: [{ type: "text", text: "c" }] },
    ]);
    expect(ids).toBeInstanceOf(Set);
    expect([...ids].sort()).toEqual(["m1", "m2"]);
  });

  it("returns an empty set for no committed assistant messages", () => {
    expect(collectCommittedMessageIds([]).size).toBe(0);
  });
});

describe("isDraftReplaced", () => {
  const draft: DraftMirror = {
    message_id: "m1",
    parent_tool_use_id: null,
    content: [{ type: "text", text: "x" }],
    toolJson: {},
  };

  it("is true when the committed set contains the draft message_id", () => {
    expect(isDraftReplaced(draft, new Set(["m0", "m1"]))).toBe(true);
  });

  it("is false when the committed set lacks the draft message_id", () => {
    expect(isDraftReplaced(draft, new Set(["m0"]))).toBe(false);
  });

  it("is false for null or message_id-less drafts", () => {
    expect(isDraftReplaced(null, new Set(["m1"]))).toBe(false);
    expect(isDraftReplaced({ ...draft, message_id: "" }, new Set(["m1"]))).toBe(false);
  });
});

describe("buildDraftTurn", () => {
  const draft: DraftMirror = {
    message_id: "msg_1",
    parent_tool_use_id: null,
    content: [{ type: "text", text: "流式中" }],
    toolJson: {},
  };

  it("builds an assistant turn from an unreplaced main-timeline draft", () => {
    const turn = buildDraftTurn(draft, false);
    expect(turn?.type).toBe("assistant");
    expect(turn?.uuid).toBe("draft-msg_1");
    expect(turn?.content[0].text).toBe("流式中");
  });

  it("returns null once the draft is marked replaced (O(1) — no entries scan)", () => {
    expect(buildDraftTurn(draft, true)).toBeNull();
  });

  it("returns null for null / empty / subagent drafts", () => {
    expect(buildDraftTurn(null, false)).toBeNull();
    expect(buildDraftTurn({ ...draft, content: [] }, false)).toBeNull();
    expect(buildDraftTurn({ ...draft, parent_tool_use_id: "tu-agent" }, false)).toBeNull();
  });

  it("does not throw when a malformed payload omits content (network boundary, `as DraftState` skips runtime validation)", () => {
    const malformed = { message_id: "msg_1", parent_tool_use_id: null } as unknown as DraftState;
    expect(buildDraftTurn(malformed, false)).toBeNull();
  });

  it("agrees with projectDraftToTurn across the committed-set derivation", () => {
    const entries: TimelineEntry[] = [
      { seq: 0, type: "assistant", message_id: "msg_1", content: [{ type: "text", text: "已提交" }] },
    ];
    const ids = collectCommittedMessageIds(entries);
    expect(buildDraftTurn(draft, isDraftReplaced(draft, ids))).toEqual(projectDraftToTurn(draft, entries));
  });
});

describe("createTimelineProjector", () => {
  let seq = 0;
  function e(partial: Omit<TimelineEntry, "seq">): TimelineEntry {
    return { seq: seq++, ...partial };
  }

  function bigImageEntry(): TimelineEntry {
    return e({
      type: "user",
      content: [{ type: "image", source: { type: "base64", media_type: "image/png", data: "A".repeat(2048) } }],
      uuid: `img-${seq}`,
    });
  }

  it("produces output value-equal to the pure projectEntriesToTurns", () => {
    const entries: TimelineEntry[] = [
      e({ type: "user", content: [{ type: "text", text: "你好" }], uuid: "u-1" }),
      e({ type: "assistant", content: [{ type: "tool_use", id: "tu-1", name: "Read", input: {} }], uuid: "a-1" }),
      e({ type: "tool_result", tool_use_id: "tu-1", content: "内容", is_error: false, uuid: "tr-1" }),
      e({ type: "assistant", content: [{ type: "text", text: "读完了" }], uuid: "a-2" }),
    ];
    const projector = createTimelineProjector();
    expect(projector.project(entries)).toEqual(projectEntriesToTurns(entries));
  });

  it("stays value-equal to the pure function across incremental appends", () => {
    const projector = createTimelineProjector();
    const entries: TimelineEntry[] = [];
    const push = (entry: TimelineEntry) => {
      entries.push(entry);
      expect(projector.project(entries)).toEqual(projectEntriesToTurns(entries));
    };
    push(e({ type: "user", content: [{ type: "text", text: "q" }], uuid: "u-1" }));
    push(e({ type: "assistant", content: [{ type: "tool_use", id: "tu-a", name: "Agent", input: {} }], uuid: "a-1" }));
    push(e({ type: "system", subtype: "task_started", task_id: "t1", tool_use_id: "tu-a", uuid: "s-1" }));
    push(e({ type: "tool_result", tool_use_id: "tu-a", content: "done", is_error: false, uuid: "tr-1" }));
    push(e({ type: "assistant", content: [{ type: "text", text: "总结" }], uuid: "a-2" }));
  });

  it("does not mutate input entries even when reusing cached clones", () => {
    const entries = [
      e({ type: "assistant", content: [{ type: "tool_use", id: "tu-1", name: "Read", input: {} }], uuid: "a-1" }),
      e({ type: "tool_result", tool_use_id: "tu-1", content: "r", is_error: false, uuid: "tr-1" }),
    ];
    const snapshot = JSON.parse(JSON.stringify(entries)) as TimelineEntry[];
    const projector = createTimelineProjector();
    projector.project(entries);
    projector.project([...entries, e({ type: "assistant", content: [{ type: "text", text: "x" }], uuid: "a-2" })]);
    expect(entries).toEqual(snapshot);
  });

  it("returns fresh turn objects each call so mutation folds never corrupt cached blocks", () => {
    // 每次 project 折叠 tool_result/task 会就地改写 turn 内的块——若复用了上次
    // 产出的块引用，第二次折叠会叠加在已折叠结果上。断言两次产出的块互不共享引用。
    const entries: TimelineEntry[] = [
      e({ type: "assistant", content: [{ type: "tool_use", id: "tu-1", name: "Read", input: {} }], uuid: "a-1" }),
    ];
    const projector = createTimelineProjector();
    const first = projector.project(entries);
    const withResult = [
      ...entries,
      e({ type: "tool_result", tool_use_id: "tu-1", content: "回填", is_error: false, uuid: "tr-1" }),
    ];
    const second = projector.project(withResult);
    // 第一次产出的 tool_use 块不应被第二次折叠回填污染
    expect(first[0].content[0].result).toBeUndefined();
    expect(second[0].content[0].result).toBe("回填");
    expect(first[0].content[0]).not.toBe(second[0].content[0]);
  });

  it("reuses the same deep-cloned block across projections instead of re-cloning (perf contract)", () => {
    // 大 base64 image block：投影器应缓存首帧深拷贝，稳定条目重投影不再触发深拷贝。
    // 通过桩化 structuredClone 计数验证：同一 entries 引用第二次 project 深拷贝次数为 0。
    const img = bigImageEntry();
    const entries: TimelineEntry[] = [img];
    const projector = createTimelineProjector();

    const original = globalThis.structuredClone;
    let cloneCalls = 0;
    globalThis.structuredClone = ((v: unknown) => {
      cloneCalls++;
      return original(v);
    }) as typeof structuredClone;
    try {
      projector.project(entries);
      expect(cloneCalls).toBeGreaterThan(0);
      // 稳定重投影：老条目的块已缓存 → 不再深拷贝
      cloneCalls = 0;
      projector.project(entries);
      expect(cloneCalls).toBe(0);
      // 追加新条目重投影：老 image 块仍复用缓存，深拷贝只作用于新条目而非 2KB 图像
      const next = [...entries, e({ type: "assistant", content: [{ type: "text", text: "hi" }], uuid: "a-1" })];
      cloneCalls = 0;
      projector.project(next);
      // 新增的是纯文本块（浅结构，无 base64），旧 image 块 0 次重拷 → 总深拷贝 ≤ 1
      expect(cloneCalls).toBeLessThanOrEqual(1);
    } finally {
      globalThis.structuredClone = original;
    }
  });

  it("evicts cache entries for seqs no longer present (bounded memory on reset)", () => {
    const projector = createTimelineProjector();
    const first = [e({ type: "user", content: [{ type: "text", text: "旧会话" }], uuid: "u-1" })];
    projector.project(first);
    // 切换会话：全新 entries，旧 seq 不再出现
    const second = [e({ type: "assistant", content: [{ type: "text", text: "新会话" }], uuid: "a-9" })];
    const out = projector.project(second);
    expect(out).toEqual(projectEntriesToTurns(second));
    expect(projector.size).toBe(1);
  });

  it("returns the same array reference for a repeated stable projection", () => {
    const projector = createTimelineProjector();
    const entries = [e({ type: "user", content: [{ type: "text", text: "hi" }], uuid: "u-1" })];
    const first = projector.project(entries);
    expect(projector.project(entries)).toBe(first);
  });

  it("stays equivalent to fresh replay through incremental subagent anchoring", () => {
    // 覆盖增量路径最复杂的分支：锚点先于子条目、子条目先于锚点（合成卡片→回收）、
    // 嵌套 subagent、task 折叠、跨 turn question_answer 回填。
    const stream: TimelineEntry[] = [
      e({ type: "user", content: [{ type: "text", text: "开始" }], uuid: "u-1" }),
      // 孤儿子时间线：锚点尚不存在 → 合成卡片
      e({ type: "assistant", content: [{ type: "text", text: "孤儿" }], uuid: "sa-1", parent_tool_use_id: "tu-late" }),
      // 锚点补到 → 合成卡片应并回主时间线
      e({ type: "assistant", content: [{ type: "tool_use", id: "tu-late", name: "Agent", input: {} }], uuid: "a-1" }),
      e({ type: "system", subtype: "task_started", task_id: "t1", tool_use_id: "tu-late", description: "外层", uuid: "s-1" }),
      // 嵌套 subagent：内层锚点在外层子时间线内
      e({
        type: "assistant",
        content: [{ type: "tool_use", id: "tu-inner", name: "Agent", input: {} }],
        uuid: "sa-2",
        parent_tool_use_id: "tu-late",
      }),
      e({ type: "assistant", content: [{ type: "text", text: "内层回复" }], uuid: "sa-3", parent_tool_use_id: "tu-inner" }),
      // 提问 → 中断把 turn 提前 flush → 答复跨 turn 回填
      e({
        type: "assistant",
        content: [{ type: "tool_use", id: "tu-q", name: "AskUserQuestion", input: {} }],
        uuid: "a-2",
      }),
      e({ type: "system", subtype: "interrupt", uuid: "i-1" }),
      e({
        type: "user",
        subtype: "question_answer",
        tool_use_id: "tu-q",
        content: "answered",
        is_error: false,
        answers: { q: "yes" },
        uuid: "qa-1",
      }),
      e({ type: "tool_result", tool_use_id: "tu-late", content: "外层完成", is_error: false, uuid: "tr-1" }),
      e({ type: "assistant", content: [{ type: "text", text: "总结" }], uuid: "a-3" }),
    ];
    const projector = createTimelineProjector();
    for (let i = 1; i <= stream.length; i++) {
      const prefix = stream.slice(0, i);
      expect(projector.project(prefix), `前缀长度 ${i} 应与全量重放一致`).toEqual(projectEntriesToTurns(prefix));
    }
  });

  it("matches fresh replay at every prefix of seeded random entry streams", () => {
    for (const seed of [7, 42, 1058]) {
      const stream = generateEntryStream(seed, 80);
      const projector = createTimelineProjector();
      for (let i = 1; i <= stream.length; i++) {
        const prefix = stream.slice(0, i);
        expect(projector.project(prefix), `seed=${seed} 前缀长度 ${i} 应与全量重放一致`).toEqual(
          projectEntriesToTurns(prefix),
        );
      }
    }
  });

  it("degrades to a synthetic card instead of dropping the whole timeline when parent_tool_use_id self-references", () => {
    // 条目的 parent_tool_use_id 等于自身 tool_use id：畸形/自指数据，真实
    // server 写入点不会产生，但投影器不应把整段时间线静默丢空。
    const entries: TimelineEntry[] = [
      e({
        type: "assistant",
        content: [{ type: "tool_use", id: "tu-self", name: "Agent", input: {} }],
        uuid: "a-1",
        parent_tool_use_id: "tu-self",
      }),
    ];
    const turns = projectEntriesToTurns(entries);
    expect(turns).toHaveLength(1);
    expect(turns[0].content[0]).toMatchObject({ type: "tool_use", id: "tu-self" });
  });

  it("degrades to synthetic cards instead of dropping the whole timeline when two subagent groups mutually anchor each other", () => {
    // entry0 属于 tu-A 的子时间线、自身携带 tool_use tu-B；entry1 属于 tu-B
    // 的子时间线、自身携带 tool_use tu-A —— 两组互相锚定成环，main 为空。
    const entries: TimelineEntry[] = [
      e({ type: "assistant", content: [{ type: "tool_use", id: "tu-b", name: "Agent", input: {} }], uuid: "a-1", parent_tool_use_id: "tu-a" }),
      e({ type: "assistant", content: [{ type: "tool_use", id: "tu-a", name: "Agent", input: {} }], uuid: "a-2", parent_tool_use_id: "tu-b" }),
    ];
    const turns = projectEntriesToTurns(entries);
    expect(turns.length).toBeGreaterThan(0);
    const projector = createTimelineProjector();
    expect(projector.project(entries)).toEqual(turns);
  });

  it("keeps compose()'s pending-group scan bounded to currently-unanchored groups, not total historical subagent count", () => {
    // 一批 subagent 组全部锚定完成后，projector.size 之外还应有一个可观测
    // 信号证明 compose() 不再对已锚定完成的历史组做全表扫描：直接量 size
    // 属性只反映 entries 数，这里改为验证锚定后继续追加主时间线消息时输出
    // 仍与全量重放一致（锚定组已完成不应再出现在顶层合成卡片里）。
    const entries: TimelineEntry[] = [];
    const projector = createTimelineProjector();
    for (let i = 0; i < 50; i++) {
      const toolId = `tu-${i}`;
      entries.push(e({ type: "assistant", content: [{ type: "tool_use", id: toolId, name: "Agent", input: {} }], uuid: `a-${i}` }));
      entries.push(e({ type: "assistant", content: [{ type: "text", text: `子回复${i}` }], uuid: `sa-${i}`, parent_tool_use_id: toolId }));
      entries.push(e({ type: "tool_result", tool_use_id: toolId, content: "done", is_error: false, uuid: `tr-${i}` }));
    }
    entries.push(e({ type: "assistant", content: [{ type: "text", text: "主时间线消息" }], uuid: "a-final" }));
    const turns = projector.project(entries);
    expect(turns).toEqual(projectEntriesToTurns(entries));
    // 全部 50 个 subagent 组都已锚定在各自 tool_use 块上，顶层不应再出现
    // 任何合成卡片（system + subagent-* uuid）。
    expect(turns.filter((t) => typeof t.uuid === "string" && t.uuid.startsWith("subagent-"))).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// 随机条目流生成器 — 线性同余伪随机（种子可复现），覆盖各条目类型组合。
// ---------------------------------------------------------------------------

function generateEntryStream(seed: number, length: number): TimelineEntry[] {
  let state = seed >>> 0;
  const rng = (): number => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
  const pick = <T,>(items: T[]): T => items[Math.floor(rng() * items.length)];
  const chance = (p: number): boolean => rng() < p;

  const entries: TimelineEntry[] = [];
  const toolIds: string[] = [];
  const taskIds: string[] = [];
  let toolCounter = 0;
  let taskCounter = 0;

  for (let seq = 0; seq < length; seq++) {
    let entry: TimelineEntry;
    const roll = rng();
    if (roll < 0.35) {
      if (chance(0.5)) {
        entry = {
          seq,
          type: "assistant",
          uuid: `a-${seq}`,
          message_id: chance(0.5) ? `msg-${seq}` : undefined,
          content: [{ type: "text", text: `文本${seq}` }],
        };
      } else {
        const id = `tu-${toolCounter++}`;
        toolIds.push(id);
        entry = {
          seq,
          type: "assistant",
          uuid: `a-${seq}`,
          content: [{ type: "tool_use", id, name: pick(["Read", "Agent", "Skill", "AskUserQuestion"]), input: { n: seq } }],
        };
      }
    } else if (roll < 0.5) {
      entry = {
        seq,
        type: "tool_result",
        uuid: `tr-${seq}`,
        tool_use_id: toolIds.length > 0 && chance(0.8) ? pick(toolIds) : `tu-ghost-${seq}`,
        content: `结果${seq}`,
        is_error: chance(0.2),
      };
    } else if (roll < 0.72) {
      const sub = rng();
      if (sub < 0.15) {
        entry = { seq, type: "system", subtype: "interrupt", uuid: `i-${seq}` };
      } else if (sub < 0.3) {
        entry = {
          seq,
          type: "system",
          subtype: "skill_invocation",
          skill_name: "demo",
          skill_args: `s${seq}`,
          tool_use_id: toolIds.length > 0 && chance(0.5) ? pick(toolIds) : null,
          uuid: `sk-${seq}`,
        };
      } else if (sub < 0.4) {
        // 未知子类型：投影应忽略
        entry = { seq, type: "system", subtype: "noise", uuid: `n-${seq}` };
      } else {
        const started = taskIds.length === 0 || chance(0.4);
        const taskId = started ? `t-${taskCounter++}` : pick(taskIds);
        if (started) taskIds.push(taskId);
        entry = {
          seq,
          type: "system",
          subtype: started ? "task_started" : pick(["task_progress", "task_notification"]),
          task_id: taskId,
          tool_use_id: toolIds.length > 0 && chance(0.6) ? pick(toolIds) : undefined,
          description: `任务${taskId}`,
          summary: chance(0.5) ? `进展${seq}` : undefined,
          task_status: chance(0.3) ? "completed" : undefined,
          usage: chance(0.5) ? { total_tokens: seq } : undefined,
          uuid: `t-${seq}`,
        };
      }
    } else if (roll < 0.85) {
      entry = {
        seq,
        type: "user",
        subtype: "question_answer",
        tool_use_id: toolIds.length > 0 && chance(0.7) ? pick(toolIds) : `tu-ghost-${seq}`,
        content: `答复${seq}`,
        is_error: chance(0.3),
        answers: chance(0.5) ? { [`q${seq}`]: "选项" } : undefined,
        uuid: `qa-${seq}`,
      };
    } else {
      entry = { seq, type: "user", uuid: `u-${seq}`, content: [{ type: "text", text: `用户${seq}` }] };
    }
    // 30% 概率归入某已存在锚点的子时间线，偶发 ghost 组
    if (toolIds.length > 0 && chance(0.3)) entry.parent_tool_use_id = pick(toolIds);
    else if (chance(0.05)) entry.parent_tool_use_id = `ghost-${Math.floor(rng() * 3)}`;
    entries.push(entry);
  }
  return entries;
}

describe("mergeEntriesBySeq", () => {
  function e(seq: number, uuid: string): TimelineEntry {
    return { seq, type: "user", uuid };
  }

  it("unions two entry lists sorted by seq without duplicates", () => {
    const merged = mergeEntriesBySeq([e(0, "a"), e(2, "c")], [e(1, "b"), e(2, "c-dup"), e(3, "d")]);
    expect(merged.map((x) => x.seq)).toEqual([0, 1, 2, 3]);
    // 同 seq 条目不可变，保留既有引用
    expect(merged[2].uuid).toBe("c");
  });

  it("keeps newer local entries when a stale cold read arrives late", () => {
    // 冷读整帧只到 seq 1，本地已有发送响应的 seq 2 —— 并集不丢新条目
    const merged = mergeEntriesBySeq([e(2, "sent")], [e(0, "a"), e(1, "b")]);
    expect(merged.map((x) => x.seq)).toEqual([0, 1, 2]);
  });

  it("handles empty sides", () => {
    expect(mergeEntriesBySeq([], [e(1, "b"), e(0, "a")]).map((x) => x.seq)).toEqual([0, 1]);
    const existing = [e(0, "a")];
    expect(mergeEntriesBySeq(existing, [])).toBe(existing);
  });
});
