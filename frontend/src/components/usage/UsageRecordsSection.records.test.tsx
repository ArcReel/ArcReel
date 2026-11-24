import { seedTaskState } from "@/test/task-state";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import type { UsageRecordsQuery } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageRecordsStore } from "@/stores/usage-records-store";
import { createDeferred } from "@/test/deferred";
import { makeTask } from "@/test/factories";
import type { EpisodeItemRef, UsageRecordPage } from "@/types";
import { makeUsageRecord, makeUsageSummary } from "./usage-fixtures";
import { renderUsageRecordsSection } from "./usage-test-utils";

const EMPTY_PAGE: UsageRecordPage = { items: [], next_cursor: null, total: 0 };

function itemRef(itemId: string, position: number): EpisodeItemRef {
  return {
    episode_title: "",
    episode_position: 1,
    item_id: itemId,
    item_position: position,
    item_excerpt: "",
    item_thumbnail: null,
  };
}

async function rowOfItem(label: string): Promise<HTMLTableRowElement> {
  return (await screen.findByRole("img", { name: label })).closest("tr") as HTMLTableRowElement;
}

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
    useTasksStore.getState().setRefreshScope({ projectName: null });
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

  it("shows incomplete active coverage until the next complete snapshot", async () => {
    vi.spyOn(API, "getActiveTasks").mockResolvedValueOnce({items: [], truncated: true}).mockResolvedValue({items: [], truncated: false});
    vi.spyOn(API, "listTasks").mockResolvedValue({ items: [], total: 0, page: 1, page_size: 200 });
    vi.spyOn(API, "getTaskStats").mockResolvedValue({stats: useTasksStore.getInitialState().stats});
    renderUsageRecordsSection();
    expect(await screen.findByText("进行中任务未完整加载。")).toBeInTheDocument();
    await act(useTasksStore.getState().refreshTasks);
    expect(screen.queryByText("进行中任务未完整加载。")).not.toBeInTheDocument();
  });

  it("renders the KPI strip from the summary response with the primary currency", async () => {
    renderUsageRecordsSection();

    expect(await screen.findByText("73")).toBeInTheDocument();
    expect(screen.getByText("90.3%")).toBeInTheDocument();
    expect(screen.getByText("65 成功")).toBeInTheDocument();
    expect(screen.getByText("另 1 次取消")).toBeInTheDocument();
    expect(screen.getByText("参考费用 · CNY")).toBeInTheDocument();
    expect(screen.getByText("+ $4.59")).toBeInTheDocument();
  });

  it("renders each record column and the failure phrase for a known error code", async () => {
    recordsPage = {
      items: [
        makeUsageRecord({
          id: 7,
          project_name: "rain-detective",
          project_title: "雨夜侦探",
          media_type: "video",
          provider: "minimax",
          model: "hailuo-02",
          status: "failed",
          error_code: "content_policy",
          error_message: "HTTP 400",
          segment_id: "E2S07",
          segment_ref: itemRef("S07", 2),
          duration_ms: 41_000,
          cost_amount: 3.6,
          currency: "CNY",
        }),
      ],
      next_cursor: null,
      total: 1,
    };
    renderUsageRecordsSection();

    const row = await rowOfItem("分镜 2");
    expect(row).not.toBeNull();
    expect(row).not.toHaveTextContent("S07");
    const cells = within(row);
    expect(cells.getByText("雨夜侦探")).toBeInTheDocument();
    expect(cells.queryByText("rain-detective")).not.toBeInTheDocument();
    expect(cells.getByText("MiniMax")).toBeInTheDocument();
    expect(cells.getByText("hailuo-02")).toBeInTheDocument();
    expect(cells.getByText("失败")).toBeInTheDocument();
    expect(cells.getByText("内容策略")).toBeInTheDocument();
    expect(cells.getByText("41秒")).toBeInTheDocument();
    expect(cells.getByText(/3\.60/)).toBeInTheDocument();
  });

  it("tells an unpriced call (null cost) from a free one (0) in the cost column", async () => {
    recordsPage = {
      items: [
        makeUsageRecord({
          id: 1,
          segment_id: "E1S01",
          segment_ref: itemRef("S01", 4),
          cost_amount: null,
          unpriced_reason: "missing_rate",
        }),
        makeUsageRecord({ id: 2, segment_id: "E1S02", segment_ref: itemRef("S02", 5), cost_amount: 0 }),
        makeUsageRecord({ id: 3, segment_id: "E1S03", segment_ref: itemRef("S03", 6), cost_amount: 0.5 }),
      ],
      next_cursor: null,
      total: 3,
    };
    renderUsageRecordsSection();

    const unpriced = within(await rowOfItem("分镜 4"));
    const free = within(await rowOfItem("分镜 5"));
    const paid = within(await rowOfItem("分镜 6"));
    expect(unpriced.getByText("未定价")).toBeInTheDocument();
    expect(free.queryByText("未定价")).not.toBeInTheDocument();
    expect(free.getByText("$0.00")).toBeInTheDocument();
    expect(paid.getByText("$0.50")).toBeInTheDocument();
  });

  it("keeps the raw failure message out of the row when there is no phrase for it", async () => {
    recordsPage = {
      items: [
        makeUsageRecord({
          segment_ref: itemRef("S10", 3),
          status: "failed",
          error_code: null,
          error_message: "provider exploded",
        }),
      ],
      next_cursor: null,
      total: 1,
    };
    renderUsageRecordsSection();

    const row = await rowOfItem("分镜 3");
    expect(row).toHaveTextContent("失败");
    expect(row).not.toHaveTextContent("provider exploded");
  });

  it("shows a dash as the target when the record has neither segment nor purpose", async () => {
    recordsPage = {
      items: [makeUsageRecord({ segment_id: null, purpose: null })],
      next_cursor: null,
      total: 1,
    };
    renderUsageRecordsSection();

    await waitFor(() => expect(screen.getAllByRole("row").length).toBeGreaterThan(1));
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

    expect(await screen.findByText("1～20 / 73")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "下一页" })).toBeEnabled();

    await userEvent.click(screen.getByRole("button", { name: "下一页" }));

    expect(await screen.findByText("21～40 / 73")).toBeInTheDocument();
  });

  it("removes a failed download when the same task resumes in another tab", async () => {
    const target = itemRef("S06", 6);
    const failedTask = makeTask({
      task_id: "t-retry",
      project_name: "星海列车",
      status: "failed",
      media_type: "video",
      task_type: "video",
      resource_id: "E1S06",
      resource_ref: target,
      updated_at: "2026-11-24T08:00:00.123400Z",
    });
    const runningTask = {
      ...failedTask,
      status: "running" as const,
      updated_at: "2026-11-24T08:00:00.123401Z",
    };
    let task = failedTask;
    vi.spyOn(API, "getActiveTasks").mockImplementation(async () => ({
      items: task.status === "running" ? [task] : [],
      truncated: false,
    }));
    vi.spyOn(API, "listTasks").mockImplementation(async () => ({
      items: [task], total: 1, page: 1, page_size: 200,
    }));
    vi.spyOn(API, "getTaskStats").mockResolvedValue({ stats: useTasksStore.getInitialState().stats });
    recordsPage = {
      items: [makeUsageRecord({
        task_id: failedTask.task_id,
        status: "failed",
        error_code: "download_failed",
        segment_ref: target,
      })],
      next_cursor: null,
      total: 1,
    };
    renderUsageRecordsSection();

    expect(await screen.findByText("下载失败")).toBeInTheDocument();

    task = runningTask;
    recordsPage = EMPTY_PAGE;
    await act(useTasksStore.getState().refreshTasks);

    expect(screen.getByText("待解析")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("下载失败")).not.toBeInTheDocument());
    expect(screen.getAllByRole("img", { name: "分镜 6" })).toHaveLength(1);
  });

  it("keeps the requested page when a download resumes during pagination", async () => {
    const failed = makeUsageRecord({
      task_id: "t-retry",
      status: "failed",
      error_code: "download_failed",
      segment_ref: itemRef("S06", 6),
    });
    recordsPage = { items: [failed], next_cursor: "cursor-2", total: 73 };
    const nextPage = createDeferred<UsageRecordPage>();
    vi.mocked(API.getUsageRecords).mockImplementation(async (query) => {
      if (query?.cursor === "cursor-2") return nextPage.promise;
      return isPendingQuery(query) ? pendingPage : recordsPage;
    });
    renderUsageRecordsSection();
    expect(await screen.findByText("下载失败")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    act(() => seedTaskState({
      tasks: [makeTask({ task_id: "t-retry", status: "running", media_type: "video" })],
    }));
    await act(async () => nextPage.resolve({
      items: [makeUsageRecord({ segment_ref: itemRef("S12", 12) })],
      next_cursor: null,
      total: 73,
    }));

    expect(await screen.findByText("21～21 / 73")).toBeInTheDocument();
    expect(await rowOfItem("分镜 12")).toBeInTheDocument();
  });

  it("puts running tasks and task-less pending calls above the finished records", async () => {
    seedTaskState({
      tasks: [
        makeTask({
          task_id: "t-1",
          status: "running",
          project_name: "星海列车",
          media_type: "video",
          resource_id: "E1S13",
          resource_ref: itemRef("S13", 6),
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
      items: [makeUsageRecord({ id: 1, segment_id: "E1S01", segment_ref: itemRef("S01", 1) })],
      next_cursor: null,
      total: 1,
    };
    renderUsageRecordsSection();

    await screen.findByRole("img", { name: "分镜 6" });
    const rows = screen.getAllByRole("row").slice(1);
    const indexOfItem = (label: string) =>
      rows.findIndex((row) => within(row).queryByRole("img", { name: label }) !== null);
    const scriptIndex = rows.findIndex((row) => row.textContent?.includes("剧本生成"));
    expect(indexOfItem("分镜 6")).toBeGreaterThanOrEqual(0);
    expect(indexOfItem("分镜 6")).toBeLessThan(scriptIndex);
    expect(scriptIndex).toBeLessThan(indexOfItem("分镜 1"));
    expect(screen.getByText("待解析")).toBeInTheDocument();
  });

  it("keeps the in-progress block outside the time range filter", async () => {
    seedTaskState({
      tasks: [
        makeTask({
          task_id: "t-old",
          status: "queued",
          project_name: "星海列车",
          queued_at: "2020-01-01T00:00:00+00:00",
          resource_id: "E9S99",
          resource_ref: itemRef("S99", 2),
        }),
      ],
    });
    renderUsageRecordsSection("section=usage&u_range=7d");

    expect(await screen.findByRole("img", { name: "分镜 2" })).toBeInTheDocument();
  });

  it("keeps only running tasks whose model matches the model filter", async () => {
    seedTaskState({
      tasks: [
        makeTask({ task_id: "t-match", status: "running", model: "veo-3", resource_id: "E1S01", resource_ref: itemRef("S01", 1) }),
        makeTask({ task_id: "t-other", status: "running", model: "kling", resource_id: "E1S02", resource_ref: itemRef("S02", 2) }),
        makeTask({ task_id: "t-queued", status: "queued", resource_id: "E1S03", resource_ref: itemRef("S03", 3) }),
      ],
    });
    renderUsageRecordsSection("section=usage&u_model=veo-3");

    expect(await screen.findByRole("img", { name: "分镜 1" })).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "分镜 2" })).not.toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "分镜 3" })).not.toBeInTheDocument();
  });

  it("leaves local render tasks out of the in-progress block", async () => {
    seedTaskState({
      tasks: [
        makeTask({ task_id: "t-video", status: "running", resource_id: "E1S01", resource_ref: itemRef("S01", 1) }),
        makeTask({
          task_id: "t-render",
          status: "running",
          task_type: "render_final_cut",
          media_type: "render",
          resource_id: "tl-0000abcd.without_narration.no_subtitles",
          provider_id: "render",
        }),
      ],
    });
    renderUsageRecordsSection();

    expect(await screen.findByRole("img", { name: "分镜 1" })).toBeInTheDocument();
    expect(screen.queryByText(/tl-0000abcd/)).not.toBeInTheDocument();
    expect(screen.getAllByText("待解析")).toHaveLength(1);
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
    expect(screen.queryByText(/1～0 \/ 1/)).not.toBeInTheDocument();
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
      seedTaskState({
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
    seedTaskState({
      tasks: [makeTask({ task_id: "t-a", status: "running", resource_id: "E1S01" })],
    });
    renderUsageRecordsSection();
    await waitFor(() => expect(API.getUsageRecords).toHaveBeenCalled());
    const busy = vi.mocked(API.getUsageRecords).mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await act(async () => {
      seedTaskState({
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
    seedTaskState({
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
