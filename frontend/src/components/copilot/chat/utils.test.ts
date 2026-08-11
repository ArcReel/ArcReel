import { describe, expect, it } from "vitest";
import type { Turn } from "@/types";
import { canEditUserTurn, composeAllTurns, turnPlainText } from "./utils";

const userTurn: Turn = {
  type: "user",
  content: [{ type: "text", text: "你是谁?" }],
  uuid: "u-1",
};

const assistantDraft: Turn = {
  type: "assistant",
  content: [{ type: "text", text: "我是 ArcReel..." }],
  uuid: "draft-1",
};

const interruptTurn: Turn = {
  type: "system",
  content: [{ type: "interrupt_notice" }],
  uuid: "sys-1",
};

const taskProgressSysTurn: Turn = {
  type: "system",
  content: [{ type: "task_progress", task_id: "t1", status: "task_started" }],
  uuid: "sys-2",
};

describe("composeAllTurns", () => {
  it("returns turns unchanged when draft is null", () => {
    expect(composeAllTurns([userTurn], null)).toEqual([userTurn]);
  });

  it("appends draft at end when last turn is not interrupt_notice", () => {
    expect(composeAllTurns([userTurn], assistantDraft)).toEqual([
      userTurn,
      assistantDraft,
    ]);
  });

  it("inserts draft before interrupt_notice when last turn is interrupt_notice", () => {
    expect(
      composeAllTurns([userTurn, interruptTurn], assistantDraft),
    ).toEqual([userTurn, assistantDraft, interruptTurn]);
  });

  it("does not reorder for non-interrupt system turns", () => {
    expect(
      composeAllTurns([userTurn, taskProgressSysTurn], assistantDraft),
    ).toEqual([userTurn, taskProgressSysTurn, assistantDraft]);
  });

  it("handles empty turns with draft", () => {
    expect(composeAllTurns([], assistantDraft)).toEqual([assistantDraft]);
  });
});

describe("turnPlainText", () => {
  it("joins text blocks and ignores non-text blocks", () => {
    const turn: Turn = {
      type: "user",
      uuid: "u-9",
      content: [
        { type: "text", text: "第一段" },
        { type: "image", source: { type: "base64", media_type: "image/png", data: "x" } },
        { type: "text", text: "第二段" },
      ],
    };
    expect(turnPlainText(turn)).toBe("第一段\n\n第二段");
  });
});

describe("canEditUserTurn", () => {
  const idle = { sessionStatus: null, hasPendingQuestion: false, isSending: false } as const;

  it("allows editing a settled user message", () => {
    expect(canEditUserTurn(userTurn, idle)).toBe(true);
  });

  it("rejects assistant and system turns", () => {
    expect(canEditUserTurn(assistantDraft, idle)).toBe(false);
    expect(canEditUserTurn(interruptTurn, idle)).toBe(false);
  });

  it("rejects a turn without uuid — there is no anchor to rewrite from", () => {
    expect(canEditUserTurn({ ...userTurn, uuid: undefined }, idle)).toBe(false);
  });

  it("rejects question answers — they are questionnaire receipts, not user messages", () => {
    const answerTurn: Turn = {
      type: "user",
      uuid: "u-2",
      content: [{ type: "question_answer", answers: { "格式？": "摘要" }, text: "摘要" }],
    };
    expect(canEditUserTurn(answerTurn, idle)).toBe(false);
  });

  it("rejects a turn with no plain text", () => {
    const imageOnly: Turn = {
      type: "user",
      uuid: "u-3",
      content: [{ type: "image", source: { type: "base64", media_type: "image/png", data: "x" } }],
    };
    expect(canEditUserTurn(imageOnly, idle)).toBe(false);
  });

  it("hides the entry while the agent is running", () => {
    expect(canEditUserTurn(userTurn, { ...idle, sessionStatus: "running" })).toBe(false);
  });

  it("hides the entry while a question card is pending", () => {
    expect(canEditUserTurn(userTurn, { ...idle, hasPendingQuestion: true })).toBe(false);
  });

  it("hides sibling entries while a send or rewrite is in flight", () => {
    // 放行的话，点别处的编辑会顶掉正在提交的编辑器，草稿随之消失
    expect(canEditUserTurn(userTurn, { ...idle, isSending: true })).toBe(false);
  });
});
