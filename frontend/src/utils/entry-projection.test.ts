import { describe, expect, it } from "vitest";
import type { DraftDeltaPayload, TimelineEntry } from "@/types";
import {
  applyDraftDelta,
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

  it("converts interrupt echo into interrupt_notice system turn and dedups adjacent echoes", () => {
    const turns = projectEntriesToTurns([
      userEntry("做点什么"),
      userEntry("[Request interrupted by user]"),
      userEntry("[Request interrupted by user for tool use]"),
    ]);
    expect(turns.map((t) => t.type)).toEqual(["user", "system"]);
    expect(turns[1].content).toEqual([{ type: "interrupt_notice" }]);
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

  it("parses task-notification XML user entries and updates the existing task block", () => {
    const xml =
      "<task-notification><task-id>t9</task-id><tool-use-id>tu-9</tool-use-id>" +
      "<status>completed</status><summary>子任务完成</summary></task-notification>";
    const turns = projectEntriesToTurns([
      entry({ type: "assistant", content: [{ type: "text", text: "spawn" }], uuid: "a-1" }),
      entry({ type: "system", subtype: "task_started", task_id: "t9", description: "d", uuid: "s-1" }),
      userEntry(xml),
    ]);
    expect(turns).toHaveLength(1);
    const taskBlocks = turns[0].content.filter((b) => b.type === "task_progress");
    expect(taskBlocks).toHaveLength(1);
    expect(taskBlocks[0].summary).toBe("子任务完成");
    expect(taskBlocks[0].task_status).toBe("completed");
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
