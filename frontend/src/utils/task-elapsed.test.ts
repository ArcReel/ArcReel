import { describe, it, expect } from "vitest";
import { taskElapsed, elapsedDisplay } from "./task-elapsed";
import type { TaskTiming } from "./task-elapsed";

const NOW = Date.parse("2026-01-01T00:10:00Z");

function timing(overrides: Partial<TaskTiming>): TaskTiming {
  return {
    status: "queued",
    queued_at: "2026-01-01T00:00:00",
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

describe("taskElapsed", () => {
  it("排队任务按 queued_at 计等待时长", () => {
    expect(taskElapsed(timing({}), NOW)).toEqual({ kind: "queued", ms: 600_000 });
  });

  it("执行中任务按 started_at 计运行时长，无时区后缀的时间戳按 UTC 解析", () => {
    const task = timing({ status: "running", started_at: "2026-01-01T00:09:00" });
    expect(taskElapsed(task, NOW)).toEqual({ kind: "running", ms: 60_000 });
  });

  it("取消中任务与执行中同口径", () => {
    const task = timing({ status: "cancelling", started_at: "2026-01-01T00:09:30" });
    expect(taskElapsed(task, NOW)).toEqual({ kind: "running", ms: 30_000 });
  });

  it("终态任务给 started_at → finished_at 的总耗时，与 now 无关", () => {
    const task = timing({
      status: "succeeded",
      started_at: "2026-01-01T00:01:00",
      finished_at: "2026-01-01T00:03:00",
    });
    expect(taskElapsed(task, NOW)).toEqual({ kind: "total", ms: 120_000 });
    expect(taskElapsed(task, NOW + 60_000)).toEqual({ kind: "total", ms: 120_000 });
  });

  it("started_at 缺失的 running 不给时长", () => {
    expect(taskElapsed(timing({ status: "running" }), NOW)).toBeNull();
  });

  it("时间戳缺失或不可解析的终态不给时长", () => {
    expect(
      taskElapsed(timing({ status: "failed", started_at: "2026-01-01T00:01:00" }), NOW),
    ).toBeNull();
    expect(
      taskElapsed(
        timing({ status: "cancelled", started_at: "not-a-date", finished_at: "also-not" }),
        NOW,
      ),
    ).toBeNull();
  });

  it("时钟回拨导致的负值收敛为 0，不产生负数时长", () => {
    const task = timing({ status: "running", started_at: "2026-01-01T00:20:00" });
    expect(taskElapsed(task, NOW)).toEqual({ kind: "running", ms: 0 });
  });
});

describe("elapsedDisplay", () => {
  it("一分钟内只给秒", () => {
    expect(elapsedDisplay(0)).toEqual({ unit: "seconds", seconds: 0 });
    expect(elapsedDisplay(59_900)).toEqual({ unit: "seconds", seconds: 59 });
  });

  it("一小时内给分秒", () => {
    expect(elapsedDisplay(60_000)).toEqual({ unit: "minutes", minutes: 1, seconds: 0 });
    expect(elapsedDisplay(3_599_000)).toEqual({ unit: "minutes", minutes: 59, seconds: 59 });
  });

  it("一小时及以上给时分，不再给秒", () => {
    expect(elapsedDisplay(3_600_000)).toEqual({ unit: "hours", hours: 1, minutes: 0 });
    expect(elapsedDisplay(9_000_000)).toEqual({ unit: "hours", hours: 2, minutes: 30 });
  });
});
