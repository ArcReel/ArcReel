import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import type { UsageRecordsQuery } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageRecordsStore } from "@/stores/usage-records-store";
import { createDeferred } from "@/test/deferred";
import { makeTask } from "@/test/factories";
import type { UsageRecordPage } from "@/types";
import { makeUsageRecord, makeUsageSummary } from "./usage-fixtures";
import { renderUsageRecordsSection } from "./usage-test-utils";

const EMPTY_PAGE: UsageRecordPage = { items: [], next_cursor: null, total: 0 };

/** 进行中区的取数不带时间范围，记录表的取数带；两者走同一个客户端方法。 */
function isPendingQuery(query: UsageRecordsQuery | undefined): boolean {
  return query?.since === undefined;
}

describe("UsageRecordsSection records", () => {
  let recordsPage: UsageRecordPage;
  let pendingPage: UsageRecordPage;

  beforeEach(() => {
    useUsageRecordsStore.setState(useUsageRecordsStore.getInitialState(), true);
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getUsageSummary").mockResolvedValue(makeUsageSummary());
    recordsPage = EMPTY_PAGE;
    pendingPage = EMPTY_PAGE;
    vi.spyOn(API, "getUsageRecords").mockImplementation(async (query) =>
      isPendingQuery(query) ? pendingPage : recordsPage,
    );
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the KPI strip from the summary response with the primary currency", async () => {
    renderUsageRecordsSection();

    expect(await screen.findByText("73")).toBeInTheDocument();
    expect(screen.getByText("90.3%")).toBeInTheDocument();
    expect(screen.getByText("65 成功")).toBeInTheDocument();
    expect(screen.getByText("另 1 次取消")).toBeInTheDocument();
    // 主币种进 kicker 与大字，其他币种只在副行列出，不折算。
    expect(screen.getByText("参考费用 · CNY")).toBeInTheDocument();
    expect(screen.getByText("+ $4.59")).toBeInTheDocument();
  });

  it("renders each record column and the failure phrase for a known error code", async () => {
    recordsPage = {
      items: [
        makeUsageRecord({
          id: 7,
          project_name: "雨夜侦探",
          media_type: "video",
          provider: "minimax",
          model: "hailuo-02",
          status: "failed",
          error_code: "content_policy",
          error_message: "HTTP 400",
          segment_id: "E2S07",
          duration_ms: 41_000,
          cost_amount: 3.6,
          currency: "CNY",
        }),
      ],
      next_cursor: null,
      total: 1,
    };
    renderUsageRecordsSection();

    const row = (await screen.findByText("分镜 E2S07")).closest("tr");
    expect(row).not.toBeNull();
    const cells = within(row as HTMLTableRowElement);
    expect(cells.getByText("雨夜侦探")).toBeInTheDocument();
    expect(cells.getByText("MiniMax")).toBeInTheDocument();
    expect(cells.getByText("hailuo-02")).toBeInTheDocument();
    expect(cells.getByText("失败")).toBeInTheDocument();
    expect(cells.getByText("内容策略")).toBeInTheDocument();
    expect(cells.getByText("41秒")).toBeInTheDocument();
    expect(cells.getByText(/3\.60/)).toBeInTheDocument();
  });

  it("falls back to the raw message when the failure has no error code", async () => {
    recordsPage = {
      items: [
        makeUsageRecord({
          status: "failed",
          error_code: null,
          error_message: "provider exploded",
        }),
      ],
      next_cursor: null,
      total: 1,
    };
    renderUsageRecordsSection();

    expect(await screen.findByText("provider exploded")).toBeInTheDocument();
  });

  it("shows a dash as the target when the record has neither segment nor purpose", async () => {
    recordsPage = {
      items: [makeUsageRecord({ segment_id: null, purpose: null })],
      next_cursor: null,
      total: 1,
    };
    renderUsageRecordsSection();

    await waitFor(() => expect(screen.getAllByRole("row").length).toBeGreaterThan(1));
    // 列序：类型 · 项目 · 目标，目标列在没有分镜也没有来源时是破折号。
    const cells = within(screen.getAllByRole("row")[1]).getAllByRole("cell");
    expect(cells[2]).toHaveTextContent("—");
  });

  it("drops the project column when the filter already pins one project", async () => {
    recordsPage = {
      items: [makeUsageRecord()],
      next_cursor: null,
      total: 1,
    };
    renderUsageRecordsSection("section=usage&u_project=%E6%98%9F%E6%B5%B7%E5%88%97%E8%BD%A6");

    await waitFor(() => expect(screen.getAllByRole("row").length).toBeGreaterThan(1));
    expect(screen.queryByRole("columnheader", { name: "项目" })).not.toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "目标" })).toBeInTheDocument();
  });

  it("shows the keyset position and disables the arrows at the ends", async () => {
    recordsPage = {
      items: Array.from({ length: 20 }, (_, i) => makeUsageRecord({ id: i + 1 })),
      next_cursor: "cursor-2",
      total: 73,
    };
    renderUsageRecordsSection();

    expect(await screen.findByText("1–20 / 73")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "下一页" })).toBeEnabled();

    await userEvent.click(screen.getByRole("button", { name: "下一页" }));

    expect(await screen.findByText("21–40 / 73")).toBeInTheDocument();
  });

  it("puts running tasks and task-less pending calls above the finished records", async () => {
    useTasksStore.setState({
      tasks: [
        makeTask({
          task_id: "t-1",
          status: "running",
          project_name: "星海列车",
          media_type: "video",
          resource_id: "E1S13",
          started_at: "2026-03-15T09:00:00+00:00",
        }),
      ],
    });
    pendingPage = {
      items: [
        makeUsageRecord({
          id: 99,
          status: "pending",
          media_type: "text",
          segment_id: null,
          purpose: "script_generation",
          started_at: "2026-03-15T08:59:00+00:00",
        }),
      ],
      next_cursor: null,
      total: 1,
    };
    recordsPage = {
      items: [makeUsageRecord({ id: 1, segment_id: "E1S01" })],
      next_cursor: null,
      total: 1,
    };
    renderUsageRecordsSection();

    await screen.findByText("分镜 E1S13");
    const targets = screen
      .getAllByRole("row")
      .slice(1)
      .map((row) => row.textContent ?? "");
    // 进行中的两行排在最前，任务行按开始时刻在无任务的 pending 调用之前。
    expect(targets.findIndex((text) => text.includes("分镜 E1S13"))).toBeLessThan(
      targets.findIndex((text) => text.includes("剧本生成")),
    );
    expect(targets.findIndex((text) => text.includes("剧本生成"))).toBeLessThan(
      targets.findIndex((text) => text.includes("分镜 E1S01")),
    );
    // 排队中的任务还没有解析出模型。
    expect(screen.getByText("待解析")).toBeInTheDocument();
  });

  it("keeps the in-progress block outside the time range filter", async () => {
    useTasksStore.setState({
      tasks: [
        makeTask({
          task_id: "t-old",
          status: "queued",
          project_name: "星海列车",
          queued_at: "2020-01-01T00:00:00+00:00",
          resource_id: "E9S99",
        }),
      ],
    });
    renderUsageRecordsSection("section=usage&u_range=7d");

    // 任务排队于七天窗口之外，但进行中区照样列出它。
    expect(await screen.findByText("分镜 E9S99")).toBeInTheDocument();
  });

  it("shows a task-less pending call only once when filtering to in-progress", async () => {
    const pending = makeUsageRecord({
      id: 99,
      status: "pending",
      segment_id: null,
      purpose: "script_generation",
    });
    pendingPage = { items: [pending], next_cursor: null, total: 1 };
    recordsPage = { items: [pending], next_cursor: null, total: 1 };

    renderUsageRecordsSection("section=usage&u_status=pending");

    expect(await screen.findAllByText("剧本生成")).toHaveLength(1);
    expect(screen.queryByText(/1–0 \/ 1/)).not.toBeInTheDocument();
  });

  it("polls pending calls every 3 seconds only while the in-progress block is non-empty", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderUsageRecordsSection();
    await waitFor(() => expect(API.getUsageRecords).toHaveBeenCalled());

    const idle = vi.mocked(API.getUsageRecords).mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000);
    });
    expect(vi.mocked(API.getUsageRecords).mock.calls.length).toBe(idle);

    await act(async () => {
      useTasksStore.setState({
        tasks: [makeTask({ task_id: "t-2", status: "running", resource_id: "E1S02" })],
      });
    });
    const busy = vi.mocked(API.getUsageRecords).mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(vi.mocked(API.getUsageRecords).mock.calls.length).toBe(busy + 1);
  });

  it("keeps the pending poll on schedule while more in-progress rows arrive", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    useTasksStore.setState({
      tasks: [makeTask({ task_id: "t-a", status: "running", resource_id: "E1S01" })],
    });
    renderUsageRecordsSection();
    await waitFor(() => expect(API.getUsageRecords).toHaveBeenCalled());
    const busy = vi.mocked(API.getUsageRecords).mock.calls.length;

    // 2 秒后又入队一个任务：行数变化不能把计时器重置回零。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await act(async () => {
      useTasksStore.setState({
        tasks: [
          makeTask({ task_id: "t-a", status: "running", resource_id: "E1S01" }),
          makeTask({ task_id: "t-b", status: "queued", resource_id: "E1S02" }),
        ],
      });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(vi.mocked(API.getUsageRecords).mock.calls.length).toBe(busy + 1);
  });

  it("refetches the records and the summary once an in-progress row settles", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const pending = makeUsageRecord({ id: 9, status: "pending", task_id: null });
    pendingPage = { items: [pending], next_cursor: null, total: 1 };

    renderUsageRecordsSection();
    await waitFor(() => expect(useUsageRecordsStore.getState().pendingRecords).toHaveLength(1));
    const summaryCalls = vi.mocked(API.getUsageSummary).mock.calls.length;
    const recordCalls = vi
      .mocked(API.getUsageRecords)
      .mock.calls.filter(([query]) => !isPendingQuery(query)).length;

    // 下一次兜底轮询发现这条调用已经落账：记录表与 KPI 也要重取，它才会出现在表里。
    pendingPage = EMPTY_PAGE;
    const settled = makeUsageRecord({ id: 9, status: "success", task_id: null });
    recordsPage = { items: [settled], next_cursor: null, total: 1 };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    await waitFor(() =>
      expect(vi.mocked(API.getUsageSummary).mock.calls.length).toBe(summaryCalls + 1),
    );
    expect(
      vi.mocked(API.getUsageRecords).mock.calls.filter(([query]) => !isPendingQuery(query)).length,
    ).toBe(recordCalls + 1);
    await waitFor(() => {
      const state = useUsageRecordsStore.getState();
      expect(state.pendingRecords).toEqual([]);
      expect(state.records.map((record) => record.id)).toEqual([9]);
    });
  });

  it("coalesces overlapping pending polls into one follow-up request", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const first = createDeferred<UsageRecordPage>();
    const second = createDeferred<UsageRecordPage>();
    let pendingCalls = 0;
    vi.mocked(API.getUsageRecords).mockImplementation((query) => {
      if (!isPendingQuery(query)) return Promise.resolve(EMPTY_PAGE);
      pendingCalls += 1;
      return pendingCalls === 1 ? first.promise : second.promise;
    });
    useTasksStore.setState({
      tasks: [makeTask({ task_id: "t-poll", status: "running", resource_id: "E1S03" })],
    });

    renderUsageRecordsSection();
    await waitFor(() => expect(pendingCalls).toBe(1));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000);
    });
    expect(pendingCalls).toBe(1);

    first.resolve(EMPTY_PAGE);
    await waitFor(() => expect(pendingCalls).toBe(2));
    second.resolve(EMPTY_PAGE);
  });
});
