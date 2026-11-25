import { seedTaskState } from "@/test/task-state";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { ToastOverlay } from "@/components/layout/ToastOverlay";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageHeaderStore } from "@/stores/usage-header-store";
import { makeTask } from "@/test/factories";
import type { EpisodeItemRef, UsageRecord, UsageSummary } from "@/types";
import { makeUsageRecord, makeUsageRecordDetail, makeUsageSummary } from "./usage-fixtures";
import {
  HEADER_PROJECT,
  renderUsageHeaderEntry,
  resetHeaderStores,
} from "./usage-header-test-utils";

const EMPTY_KPI = {
  calls: 0,
  success: 0,
  failed: 0,
  cancelled: 0,
  success_rate: null,
  cost: {},
  unpriced_calls: 0,
};

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

function stubUsageApi() {
  vi.spyOn(API, "getUsageSummary").mockResolvedValue(makeUsageSummary());
  vi.spyOn(API, "getUsageRecords").mockResolvedValue({
    items: [],
    next_cursor: null,
    total: 0,
  });
}

function openPopover(state: {
  summary?: UsageSummary;
  recent?: UsageRecord[];
  pending?: UsageRecord[];
}) {
  useUsageHeaderStore.setState({
    projectName: HEADER_PROJECT,
    summary: state.summary ?? makeUsageSummary(),
    recent: state.recent ?? [],
    pending: state.pending ?? [],
  });
  useAppStore.setState({ usagePanelOpen: true });
  return renderUsageHeaderEntry();
}

describe("UsagePopover", () => {
  beforeEach(() => {
    resetHeaderStores();
    vi.restoreAllMocks();
    stubUsageApi();
  });

  afterEach(async () => {
    await i18n.changeLanguage("zh");
  });

  it("shows incomplete active coverage until the next complete snapshot", async () => {
    vi.spyOn(API, "getActiveTasks").mockResolvedValueOnce({items: [], truncated: true}).mockResolvedValue({items: [], truncated: false});
    vi.spyOn(API, "listTasks").mockResolvedValue({ items: [], total: 0, page: 1, page_size: 200 });
    vi.spyOn(API, "getTaskStats").mockResolvedValue({stats: useTasksStore.getInitialState().stats});
    useTasksStore.getState().setRefreshScope({ projectName: HEADER_PROJECT });
    openPopover({});
    await act(useTasksStore.getState().refreshTasks);
    expect(await screen.findByText("进行中任务未完整加载。")).toBeInTheDocument();
    await act(useTasksStore.getState().refreshTasks);
    expect(screen.queryByText("进行中任务未完整加载。")).not.toBeInTheDocument();
  });

  it("shows only the guidance line and the records link when nothing was ever called", () => {
    openPopover({ summary: makeUsageSummary({ kpi: EMPTY_KPI, primary_currency: null }) });

    expect(
      screen.getByText("本项目还没有使用记录。开始生成后，这里会显示进行中与已结束的调用。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看全部记录" })).toBeInTheDocument();
    expect(screen.queryByText("调用次数")).not.toBeInTheDocument();
    expect(screen.queryByText("进行中")).not.toBeInTheDocument();
  });

  it("keeps a placeholder in the finished column while only tasks are running", () => {
    seedTaskState({
      tasks: [
        makeTask({
          task_id: "t-1",
          project_name: HEADER_PROJECT,
          status: "running",
          task_type: "storyboard",
          media_type: "image",
          resource_id: "E1S10",
          resource_ref: itemRef("S10", 3),
        }),
      ],
    });
    openPopover({ summary: makeUsageSummary({ kpi: EMPTY_KPI }) });

    const running = screen.getByRole("region", { name: "进行中" });
    expect(within(running).getByRole("img", { name: "分镜 3" })).toBeInTheDocument();
    expect(running).toHaveTextContent(/3\s*第 1 集/);
    expect(screen.getByText("还没有已结束的调用。")).toBeInTheDocument();
    expect(screen.getByText("本项目 · 全部")).toBeInTheDocument();
    expect(screen.getByText("0 成功")).toBeInTheDocument();
  });

  it("keeps a placeholder in the in-progress column while only finished calls exist", () => {
    openPopover({ recent: [makeUsageRecord({ id: 7, segment_id: "E1S11", segment_ref: itemRef("S11", 6) })] });

    expect(screen.getByText("当前没有进行中的调用。")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "分镜 6" })).toBeInTheDocument();
  });

  it("opens the same detail modal as the settings page from a finished row", async () => {
    const detailSpy = vi
      .spyOn(API, "getUsageRecord")
      .mockResolvedValue(makeUsageRecordDetail({ id: 7, prompt: "一只在雨里的猫" }));
    openPopover({ recent: [makeUsageRecord({ id: 7, segment_id: "E1S11", segment_ref: itemRef("S11", 6) })] });

    fireEvent.click(screen.getByRole("img", { name: "分镜 6" }));

    await waitFor(() => expect(detailSpy).toHaveBeenCalledWith(7, expect.anything()));
    expect(await screen.findByText("一只在雨里的猫")).toBeInTheDocument();
  });

  it("puts a failed-call phrase beside its target", () => {
    openPopover({
      recent: [
        makeUsageRecord({
          id: 7,
          segment_id: "E1S11",
          segment_ref: itemRef("S11", 6),
          status: "failed",
          error_code: "timeout",
        }),
      ],
    });

    expect(screen.getByRole("img", { name: "分镜 6" }).closest("button")).toHaveTextContent("超时");
  });

  it("renders the success rate in the same format as the settings page", async () => {
    await i18n.changeLanguage("vi");

    openPopover({});

    expect(screen.getByText("90,3%")).toBeInTheDocument();
  });

  it("renders the counts with the language's thousands separator", async () => {
    for (const [language, calls, failed] of [
      ["zh", "12,340", "1,205"],
      ["en", "12,340", "1,205"],
      ["vi", "12.340", "1.205"],
    ] as const) {
      await i18n.changeLanguage(language);
      const base = makeUsageSummary();
      const summary = makeUsageSummary({
        kpi: { ...base.kpi, calls: 12_340, failed: 1_205 },
      });
      vi.mocked(API.getUsageSummary).mockResolvedValue(summary);

      const { unmount } = openPopover({ summary });

      expect(screen.getByText(calls)).toBeInTheDocument();
      expect(screen.getByText(failed)).toBeInTheDocument();
      unmount();
    }
  });

  it("says so when a refresh fails, and clears the notice once a retry succeeds", async () => {
    vi.mocked(API.getUsageSummary).mockRejectedValueOnce(new Error("network down"));

    openPopover({});

    const notice = await screen.findByText(
      "部分数据加载失败，显示的可能不是最新结果。",
    );
    expect(notice).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "刷新" }));

    await waitFor(() =>
      expect(
        screen.queryByText("部分数据加载失败，显示的可能不是最新结果。"),
      ).not.toBeInTheDocument(),
    );
  });

  it("navigates to the settings records section prefilled with this project", () => {
    const { location } = openPopover({});

    fireEvent.click(screen.getByRole("link", { name: "查看全部记录" }));

    expect(location.history.at(-1)).toBe(
      `/app/settings?section=usage&u_project=${encodeURIComponent(HEADER_PROJECT)}`,
    );
    expect(useAppStore.getState().usagePanelOpen).toBe(false);
  });

  it.each([false, true])("hides a resumed download with history request failure=%s", async (historyFails) => {
    const failedRecord = makeUsageRecord({
      status: "failed",
      error_code: "download_failed",
      task_id: "t-retry",
    });
    const failedTask = makeTask({
      task_id: "t-retry",
      project_name: HEADER_PROJECT,
      status: "failed",
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
      items: task.status === "succeeded" ? [] : [task], total: 1, page: 1, page_size: 200,
    }));
    vi.spyOn(API, "lookupTasks").mockImplementation(async () => ({ items: [task], missing_ids: [] }));
    vi.spyOn(API, "getTaskStats").mockResolvedValue({ stats: useTasksStore.getInitialState().stats });
    vi.mocked(API.getUsageRecords).mockImplementation(async (query) => {
      if (historyFails && task.status !== "failed" && query?.statuses?.includes("failed")) {
        throw new Error("history unavailable");
      }
      const items = task.status === "failed" && query?.statuses?.includes("failed") ? [failedRecord] : [];
      return { items, next_cursor: null, total: items.length };
    });
    useTasksStore.getState().setRefreshScope({ projectName: HEADER_PROJECT });
    await act(useTasksStore.getState().refreshTasks);
    openPopover({});

    expect(await screen.findByRole("button", { name: "重试下载" })).toBeInTheDocument();

    task = runningTask;
    await act(useTasksStore.getState().refreshTasks);

    expect(within(screen.getByRole("region", { name: "进行中" })).getByText("生成中…")).toBeInTheDocument();
    const finished = within(screen.getByRole("region", { name: "最近已结束" }));
    await waitFor(() => {
      expect(finished.queryByText("失败")).not.toBeInTheDocument();
      expect(finished.queryByRole("button", { name: "重试下载" })).not.toBeInTheDocument();
    });
    expect(useUsageHeaderStore.getState().loadFailed).toBe(historyFails);

    task = { ...runningTask, status: "succeeded", updated_at: "2026-11-24T08:00:00.123402Z" };
    await act(useTasksStore.getState().refreshTasks);
    expect(finished.queryByText("失败")).not.toBeInTheDocument();
    expect(finished.queryByRole("button", { name: "重试下载" })).not.toBeInTheDocument();
  });

  it("retries a failed download through the tasks API and refetches the records", async () => {
    const failedRecord = makeUsageRecord({
      id: 8,
      segment_id: "E1S12",
      status: "failed",
      error_code: "download_failed",
      task_id: "t-9",
    });
    const retrySpy = vi
      .spyOn(API, "retryTaskDownload")
      .mockResolvedValue({ task: makeTask({ task_id: "t-9", status: "running" }) });
    vi.mocked(API.getUsageRecords).mockImplementation(async (query) => ({
      items: query?.statuses?.includes("failed") ? [failedRecord] : [],
      next_cursor: null,
      total: query?.statuses?.includes("failed") ? 1 : 0,
    }));
    openPopover({
      recent: [failedRecord],
    });

    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalledTimes(1));
    const callsBeforeRetry = vi.mocked(API.getUsageSummary).mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "重试下载" }));

    await waitFor(() => expect(retrySpy).toHaveBeenCalledWith("t-9"));
    await waitFor(() =>
      expect(API.getUsageSummary).toHaveBeenCalledTimes(callsBeforeRetry + 1),
    );
  });

  it.each([
    {
      failure: "HTTP 503",
      respond: () =>
        Response.json(
          { detail: "服务正在停机，暂不可重试下载任务 't-9'，请待服务重启后再试" },
          { status: 503 },
        ),
      message: "服务暂不可用，恢复后请重试下载",
    },
    {
      failure: "HTTP 400",
      respond: () => Response.json({ detail: "任务不符合重试下载条件" }, { status: 400 }),
      message: "重试下载没能开始，请稍后再试",
    },
    {
      failure: "a network error",
      respond: () => {
        throw new TypeError("Failed to fetch");
      },
      message: "重试下载没能开始，请稍后再试",
    },
  ])("shows the download retry notice for $failure", async ({ respond, message }) => {
    vi.spyOn(globalThis, "fetch").mockImplementationOnce(async () => respond());

    render(<ToastOverlay />);
    openPopover({
      recent: [
        makeUsageRecord({ status: "failed", error_code: "download_failed", task_id: "t-9" }),
      ],
    });

    fireEvent.click(screen.getByRole("button", { name: "重试下载" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
  });

  it("renders live text task phases from active snapshot, elapsed time, and 15s hint on controllable clock", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date("2026-11-01T12:00:10Z"));
      const textTask = makeTask({
        task_id: "t-text-1",
        project_name: HEADER_PROJECT,
        task_type: "text_drama_script_plan",
        media_type: "text",
        provider_id: "anthropic",
        model: "claude-sonnet-4",
        resource_id: "episode-1",
        status: "running",
        started_at: "2026-11-01T12:00:00Z",
        progress: { task_id: "t-text-1", seq: 1, phase: "waiting", model: "claude-sonnet-4" },
      });
      vi.spyOn(API, "getActiveTasks").mockResolvedValue({ items: [textTask], truncated: false });
      vi.spyOn(API, "listTasks").mockResolvedValue({ items: [textTask], total: 1, page: 1, page_size: 200 });
      vi.spyOn(API, "getTaskStats").mockResolvedValue({ stats: useTasksStore.getInitialState().stats });

      useTasksStore.getState().setRefreshScope({ projectName: HEADER_PROJECT });
      await act(useTasksStore.getState().refreshTasks);
      openPopover({ summary: makeUsageSummary({ kpi: EMPTY_KPI }) });

      const running = screen.getByRole("region", { name: "进行中" });
      expect(within(running).getByText("正在等待 claude-sonnet-4 响应…")).toBeInTheDocument();
      expect(within(running).getByLabelText(/已运行.*10/)).toBeInTheDocument();

      act(() =>
        useTasksStore.getState().receiveProgress({
          task_id: "t-text-1",
          seq: 2,
          phase: "receiving",
          thinking: true,
          characters: 0,
        }),
      );
      expect(within(running).getByText("模型思考中…")).toBeInTheDocument();

      act(() =>
        useTasksStore.getState().receiveProgress({
          task_id: "t-text-1",
          seq: 3,
          phase: "receiving",
          thinking: false,
          characters: 850,
        }),
      );
      expect(within(running).getByText("正在接收生成内容 · 已接收约 850 字…")).toBeInTheDocument();

      act(() =>
        useTasksStore.getState().receiveProgress({
          task_id: "t-text-1",
          seq: 4,
          phase: "retrying",
          attempt: 2,
          max_attempts: 3,
          error: "429",
        }),
      );
      expect(within(running).getByText("正在重试（第 2/3 次尝试）…")).toBeInTheDocument();

      act(() => vi.advanceTimersByTime(5_000));
      expect(within(running).queryByText(/长文本通常需要数十秒至数分钟/)).not.toBeInTheDocument();

      act(() => vi.advanceTimersByTime(1_000));
      expect(within(running).getByText(/长文本通常需要数十秒至数分钟/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("labels a text_wait_timeout failure in the recently finished list", () => {
    openPopover({
      recent: [
        makeUsageRecord({
          id: 9,
          media_type: "text",
          purpose: "script_generation",
          segment_id: null,
          status: "failed",
          error_code: "text_wait_timeout",
        }),
      ],
    });

    expect(within(screen.getByRole("region", { name: "最近已结束" })).getByText("等待超时")).toBeInTheDocument();
  });
});
