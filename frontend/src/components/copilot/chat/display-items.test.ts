import { describe, expect, it } from "vitest";
import type { ContentBlock, Turn } from "@/types";
import { buildDisplayItems } from "./display-items";

const user = (uuid: string, text: string): Turn => ({ type: "user", uuid, content: [{ type: "text", text }] });
const assistant = (uuid: string, content: ContentBlock[], timestamp?: string): Turn => ({
  type: "assistant",
  uuid,
  content,
  timestamp,
});
const text = (value: string): ContentBlock => ({ type: "text", text: value });
// 只有签名的思考块：投影里常见的空 turn，没有任何可见内容
const signedThinking: ContentBlock = { type: "thinking", thinking: "" };
const interrupt: Turn = { type: "system", uuid: "sys-interrupt", content: [{ type: "interrupt_notice" }] };

describe("buildDisplayItems", () => {
  it("merges consecutive assistant turns between two user messages into one round", () => {
    const toolUse: ContentBlock = { type: "tool_use", id: "tool-1", name: "Read", input: {} };
    const items = buildDisplayItems(
      [
        user("u-1", "生成第 1 集"),
        assistant("a-1", [text("先读计划")], "2026-01-01T08:00:00Z"),
        assistant("a-2", [toolUse], "2026-01-01T08:00:05Z"),
        assistant("a-3", [text("读完了")], "2026-01-01T08:00:09Z"),
        user("u-2", "继续"),
        assistant("a-4", [text("好的")]),
      ],
      null,
    );

    expect(items.map((item) => item.turn.type)).toEqual(["user", "assistant", "user", "assistant"]);
    expect(items[1].turn.content).toEqual([text("先读计划"), toolUse, text("读完了")]);
    // 一轮的时间取最后一段回复的时间
    expect(items[1].turn.timestamp).toBe("2026-01-01T08:00:09Z");
  });

  it("skips turns without visible content, so the replies around them join one round", () => {
    const items = buildDisplayItems(
      [
        user("u-1", "你好"),
        assistant("a-1", [text("第一段")]),
        assistant("a-2", [signedThinking]),
        { type: "system", uuid: "sys-empty", content: [] },
        assistant("a-3", [signedThinking, text("第二段")]),
      ],
      null,
    );

    expect(items).toHaveLength(2);
    expect(items[1].turn.content.filter((block) => block.type === "text")).toEqual([text("第一段"), text("第二段")]);
  });

  it("drops signed thinking from replies that have visible content, so no empty step group renders", () => {
    const items = buildDisplayItems(
      [user("u-1", "你好"), assistant("a-1", [signedThinking, text("第一段")]), assistant("a-2", [signedThinking, text("第二段")])],
      null,
    );

    expect(items[1].turn.content).toEqual([text("第一段"), text("第二段")]);
  });

  it("keeps a reply that has only signed thinking out of the list entirely", () => {
    const items = buildDisplayItems([user("u-1", "你好"), assistant("a-1", [signedThinking])], null);

    expect(items.map((item) => item.turn.type)).toEqual(["user"]);
  });

  it("keeps a streaming draft even while its thinking has no text yet", () => {
    const draft = assistant("draft-m1", [signedThinking]);
    const items = buildDisplayItems([user("u-1", "你好")], draft);

    expect(items).toHaveLength(2);
    expect(items[1].streaming).toBe(true);
  });

  it("marks the merged round as streaming when the draft continues it", () => {
    const draft = assistant("draft-m2", [text("正在写")]);
    const items = buildDisplayItems([user("u-1", "你好"), assistant("a-1", [text("已写")])], draft);

    expect(items).toHaveLength(2);
    expect(items[1].streaming).toBe(true);
    expect(items[1].turn.content).toEqual([text("已写"), text("正在写")]);
  });

  it("keeps the round identity while the streaming draft is committed", () => {
    const streaming = buildDisplayItems([user("u-1", "你好")], assistant("draft-m1", [text("第一段")]));
    const committed = buildDisplayItems(
      [user("u-1", "你好"), assistant("a-1", [text("第一段")])],
      assistant("draft-m2", [text("第二段")]),
    );

    expect(committed[1].key).toBe(streaming[1].key);
  });

  it("does not merge replies across a system event", () => {
    const items = buildDisplayItems(
      [user("u-1", "你好"), assistant("a-1", [text("第一段")]), interrupt, assistant("a-2", [text("第二段")])],
      null,
    );

    expect(items.map((item) => item.turn.type)).toEqual(["user", "assistant", "system", "assistant"]);
    expect(new Set(items.map((item) => item.key)).size).toBe(items.length);
  });

  it("keeps a compaction marker as its own item, separating the replies before and after it", () => {
    const compaction: Turn = { type: "system", uuid: "c-1", content: [{ type: "compact_summary", text: "摘要" }] };
    const items = buildDisplayItems(
      [user("u-1", "你好"), assistant("a-1", [text("压缩前")]), compaction, assistant("a-2", [text("压缩后")])],
      null,
    );

    expect(items.map((item) => item.key)).toEqual(["u-1", "u-1:reply", "c-1", "c-1:reply"]);
    expect(items[2].turn.content).toEqual(compaction.content);
  });

  it("places an interrupted draft before the interrupt marker, in the same round as its committed part", () => {
    const items = buildDisplayItems(
      [user("u-1", "你好"), assistant("a-1", [text("已写")]), interrupt],
      assistant("draft-m2", [text("写到一半")]),
    );

    expect(items.map((item) => item.turn.type)).toEqual(["user", "assistant", "system"]);
    expect(items[1].turn.content).toEqual([text("已写"), text("写到一半")]);
  });
});
