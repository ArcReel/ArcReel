import { describe, expect, it } from "vitest";
import type { Turn } from "@/types";
import { composeAllTurns } from "./utils";

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
