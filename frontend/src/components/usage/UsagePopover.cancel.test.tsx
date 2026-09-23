import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API, ApiRequestError } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageHeaderStore } from "@/stores/usage-header-store";
import { createDeferred } from "@/test/deferred";
import { makeTask } from "@/test/factories";
import type { TaskItem } from "@/types";
import { makeUsageSummary } from "./usage-fixtures";
import {
  HEADER_PROJECT,
  renderUsageHeaderEntry,
  resetHeaderStores,
} from "./usage-header-test-utils";

const STARTED_AT = "2026-04-20T00:00:00Z";

function openWithTasks(tasks: TaskItem[]) {
  useTasksStore.setState({ tasks });
  useUsageHeaderStore.setState({
    projectName: HEADER_PROJECT,
    summary: makeUsageSummary(),
  });
  useAppStore.setState({ usagePanelOpen: true });
  return renderUsageHeaderEntry();
}

function activeTask(overrides: Partial<TaskItem> = {}): TaskItem {
  return makeTask({
    task_id: "t-q",
    project_name: HEADER_PROJECT,
    media_type: "image",
    resource_id: "E1S10",
    status: "queued",
    started_at: STARTED_AT,
    ...overrides,
  });
}

const RUNNING_REJECTION = "任务 't-q' 已开始执行，不可取消；它会照常跑完并保留结果";

function mockSinglePreview() {
  vi.spyOn(API, "cancelPreview").mockResolvedValue({
    task: { task_id: "t-q", task_type: "storyboard", resource_id: "E1S10", status: "queued" },
    cascaded: [],
  });
}

describe("UsagePopover cancellation", () => {
  beforeEach(() => {
    resetHeaderStores();
    vi.restoreAllMocks();
    vi.spyOn(API, "getUsageSummary").mockResolvedValue(makeUsageSummary());
    vi.spyOn(API, "getUsageRecords").mockResolvedValue({
      items: [],
      next_cursor: null,
      total: 0,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("previews the cascade before cancelling a queued task", async () => {
    vi.spyOn(API, "cancelPreview").mockResolvedValue({
      task: { task_id: "t-q", task_type: "storyboard", resource_id: "E1S10", status: "queued" },
      cascaded: [{ task_id: "t-dep", task_type: "video", resource_id: "E1S11" }],
    });
    const cancelSpy = vi
      .spyOn(API, "cancelTask")
      .mockResolvedValue({ cancelled: [], skipped_terminal: [] });
    openWithTasks([activeTask()]);

    fireEvent.click(screen.getByRole("button", { name: "取消此任务" }));

    const dialog = await screen.findByRole("alertdialog", { name: "取消确认" });
    expect(dialog).toHaveTextContent("取消此任务将同时取消 1 个依赖任务");
    expect(dialog).toHaveTextContent("E1S11");

    fireEvent.click(screen.getByRole("button", { name: "确认取消" }));

    await waitFor(() => expect(cancelSpy).toHaveBeenCalledWith("t-q"));
  });

  it("keeps the latest cancellation preview when an older response arrives late", async () => {
    const first = createDeferred<{
      task: { task_id: string; task_type: string; resource_id: string; status: string };
      cascaded: { task_id: string; task_type: string; resource_id: string }[];
    }>();
    vi.spyOn(API, "cancelPreview").mockImplementation((taskId) =>
      taskId === "t-first"
        ? first.promise
        : Promise.resolve({
            task: {
              task_id: taskId,
              task_type: "storyboard",
              resource_id: "E1S11",
              status: "queued",
            },
            cascaded: [{ task_id: "t-new", task_type: "video", resource_id: "E1S12" }],
          }),
    );
    openWithTasks([
      activeTask({ task_id: "t-first", resource_id: "E1S10", started_at: "2026-04-20T00:00:02Z" }),
      activeTask({ task_id: "t-second", resource_id: "E1S11", started_at: "2026-04-20T00:00:01Z" }),
    ]);

    const cancelButtons = screen.getAllByRole("button", { name: "取消此任务" });
    fireEvent.click(cancelButtons[0]);
    fireEvent.click(cancelButtons[1]);
    const dialog = await screen.findByRole("alertdialog", { name: "取消确认" });
    expect(dialog).toHaveTextContent("E1S12");

    await act(async () => {
      first.resolve({
        task: {
          task_id: "t-first",
          task_type: "storyboard",
          resource_id: "E1S10",
          status: "queued",
        },
        cascaded: [{ task_id: "t-old", task_type: "video", resource_id: "E1S99" }],
      });
      await first.promise;
    });
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveTextContent("E1S12");
    expect(dialog).not.toHaveTextContent("E1S99");
  });

  it("keeps the confirmation open with a failure notice until a retry succeeds", async () => {
    mockSinglePreview();
    const cancelSpy = vi
      .spyOn(API, "cancelTask")
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce({ cancelled: [], skipped_terminal: [] });
    openWithTasks([activeTask()]);

    fireEvent.click(screen.getByRole("button", { name: "取消此任务" }));
    const dialog = await screen.findByRole("alertdialog", { name: "取消确认" });
    fireEvent.click(screen.getByRole("button", { name: "确认取消" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("取消失败，请重试");
    expect(dialog).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消此任务" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "确认取消" }));

    await waitFor(() => expect(cancelSpy).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog", { name: "取消确认" })).not.toBeInTheDocument(),
    );
  });

  it("drops a pending confirmation when the popover is closed", async () => {
    mockSinglePreview();
    openWithTasks([activeTask()]);
    fireEvent.click(screen.getByRole("button", { name: "取消此任务" }));
    await screen.findByRole("alertdialog", { name: "取消确认" });

    act(() => useAppStore.getState().setUsagePanelOpen(false));
    act(() => useAppStore.getState().setUsagePanelOpen(true));

    expect(screen.queryByRole("alertdialog", { name: "取消确认" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消此任务" })).toBeInTheDocument();
  });

  it("cancels only the queued tasks from the section header", async () => {
    vi.spyOn(API, "cancelAllPreview").mockResolvedValue({ queued_count: 2 });
    const cancelAllSpy = vi
      .spyOn(API, "cancelAllQueued")
      .mockResolvedValue({ cancelled_count: 2, skipped_running_count: 1 });
    openWithTasks([
      activeTask({ task_id: "t-run", status: "running" }),
      activeTask({ task_id: "t-q1", resource_id: "E1S11" }),
      activeTask({ task_id: "t-q2", resource_id: "E1S12" }),
    ]);

    fireEvent.click(screen.getByRole("button", { name: "取消所有排队中的任务" }));

    const dialog = await screen.findByRole("alertdialog", { name: "取消确认" });
    expect(dialog).toHaveTextContent("确定取消所有 2 个排队中的任务？");

    fireEvent.click(screen.getByRole("button", { name: "确认取消" }));

    await waitFor(() => expect(cancelAllSpy).toHaveBeenCalledWith(HEADER_PROJECT));
  });

  it("hides the cancel-all action while nothing is queued", () => {
    openWithTasks([activeTask({ status: "running" })]);

    expect(
      screen.queryByRole("button", { name: "取消所有排队中的任务" }),
    ).not.toBeInTheDocument();
  });

  it("offers no cancel action on a task that has started running", () => {
    openWithTasks([activeTask({ task_id: "t-run", status: "running" })]);

    expect(screen.getByText("生成中...")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "取消此任务" })).not.toBeInTheDocument();
  });

  it("swaps the row action for a disabled spinner while the confirmed cancel is in flight", async () => {
    mockSinglePreview();
    const pending = createDeferred<{ cancelled: TaskItem[]; skipped_terminal: TaskItem[] }>();
    vi.spyOn(API, "cancelTask").mockReturnValue(pending.promise);
    openWithTasks([activeTask()]);

    fireEvent.click(screen.getByRole("button", { name: "取消此任务" }));
    await screen.findByRole("alertdialog", { name: "取消确认" });
    fireEvent.click(screen.getByRole("button", { name: "确认取消" }));

    expect(await screen.findByRole("button", { name: "正在取消…" })).toBeDisabled();

    await act(async () => {
      pending.resolve({ cancelled: [], skipped_terminal: [] });
      await pending.promise;
    });
    expect(screen.queryByRole("button", { name: "正在取消…" })).not.toBeInTheDocument();
  });

  it("shows the server's reason when the task started running before the cancel landed", async () => {
    mockSinglePreview();
    vi.spyOn(API, "cancelTask").mockRejectedValue(
      new ApiRequestError(RUNNING_REJECTION, undefined, 409),
    );
    openWithTasks([activeTask()]);

    fireEvent.click(screen.getByRole("button", { name: "取消此任务" }));
    const dialog = await screen.findByRole("alertdialog", { name: "取消确认" });
    fireEvent.click(screen.getByRole("button", { name: "确认取消" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(RUNNING_REJECTION);
    expect(alert).not.toHaveTextContent("取消失败，请重试");
    expect(dialog).toBeInTheDocument();
  });

  it("falls back to the generic failure notice for a non-409 rejection", async () => {
    mockSinglePreview();
    vi.spyOn(API, "cancelTask").mockRejectedValue(
      new ApiRequestError("服务暂时不可用", undefined, 503),
    );
    openWithTasks([activeTask()]);

    fireEvent.click(screen.getByRole("button", { name: "取消此任务" }));
    await screen.findByRole("alertdialog", { name: "取消确认" });
    fireEvent.click(screen.getByRole("button", { name: "确认取消" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("取消失败，请重试");
    expect(alert).not.toHaveTextContent("服务暂时不可用");
  });

  it("advances the elapsed readout of a running row every second", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-20T00:00:05Z"));
    openWithTasks([activeTask({ status: "running" })]);

    expect(screen.getByText("5秒")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(3000);
    });

    expect(screen.getByText("8秒")).toBeInTheDocument();
  });
});
