import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
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

function runningTask(overrides: Partial<TaskItem> = {}): TaskItem {
  return makeTask({
    task_id: "t-run",
    project_name: HEADER_PROJECT,
    media_type: "image",
    resource_id: "E1S10",
    status: "running",
    started_at: STARTED_AT,
    ...overrides,
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

  it("previews the cascade before cancelling a single task", async () => {
    vi.spyOn(API, "cancelPreview").mockResolvedValue({
      task: { task_id: "t-run", task_type: "storyboard", resource_id: "E1S10", status: "running" },
      cascaded: [{ task_id: "t-dep", task_type: "video", resource_id: "E1S11" }],
    });
    const cancelSpy = vi
      .spyOn(API, "cancelTask")
      .mockResolvedValue({ cancelled: [], cancelling: [], skipped_terminal: [] });
    openWithTasks([runningTask()]);

    fireEvent.click(screen.getByRole("button", { name: "取消此任务" }));

    const dialog = await screen.findByRole("alertdialog", { name: "取消确认" });
    expect(dialog).toHaveTextContent("取消此任务将同时取消 1 个依赖任务");
    expect(dialog).toHaveTextContent("E1S11");

    fireEvent.click(screen.getByRole("button", { name: "确认取消" }));

    await waitFor(() => expect(cancelSpy).toHaveBeenCalledWith("t-run"));
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
              status: "running",
            },
            cascaded: [{ task_id: "t-new", task_type: "video", resource_id: "E1S12" }],
          }),
    );
    openWithTasks([
      runningTask({ task_id: "t-first", resource_id: "E1S10", started_at: "2026-04-20T00:00:02Z" }),
      runningTask({ task_id: "t-second", resource_id: "E1S11", started_at: "2026-04-20T00:00:01Z" }),
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
          status: "running",
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
    vi.spyOn(API, "cancelPreview").mockResolvedValue({
      task: { task_id: "t-run", task_type: "storyboard", resource_id: "E1S10", status: "running" },
      cascaded: [],
    });
    const cancelSpy = vi
      .spyOn(API, "cancelTask")
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce({ cancelled: [], cancelling: [], skipped_terminal: [] });
    openWithTasks([runningTask()]);

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
    vi.spyOn(API, "cancelPreview").mockResolvedValue({
      task: { task_id: "t-run", task_type: "storyboard", resource_id: "E1S10", status: "running" },
      cascaded: [],
    });
    openWithTasks([runningTask()]);
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
      runningTask(),
      runningTask({ task_id: "t-q1", status: "queued", resource_id: "E1S11" }),
      runningTask({ task_id: "t-q2", status: "queued", resource_id: "E1S12" }),
    ]);

    fireEvent.click(screen.getByRole("button", { name: "取消所有排队中的任务" }));

    const dialog = await screen.findByRole("alertdialog", { name: "取消确认" });
    expect(dialog).toHaveTextContent("确定取消所有 2 个排队中的任务？");

    fireEvent.click(screen.getByRole("button", { name: "确认取消" }));

    await waitFor(() => expect(cancelAllSpy).toHaveBeenCalledWith(HEADER_PROJECT));
  });

  it("hides the cancel-all action while nothing is queued", () => {
    openWithTasks([runningTask()]);

    expect(
      screen.queryByRole("button", { name: "取消所有排队中的任务" }),
    ).not.toBeInTheDocument();
  });

  it("keeps a cancelling task visible with a disabled spinner action", () => {
    openWithTasks([runningTask({ status: "cancelling" })]);

    expect(screen.getByRole("button", { name: "正在取消…" })).toBeDisabled();
  });

  it("expands the generation warnings of a running task", () => {
    openWithTasks([runningTask({ result: { warnings: ["首帧已降级为 1K"] } })]);

    const toggle = screen.getByRole("button", { name: /1 条生成警示/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("首帧已降级为 1K")).toBeInTheDocument();
  });

  it("advances the elapsed readout of a running row every second", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-20T00:00:05Z"));
    openWithTasks([runningTask()]);

    expect(screen.getByText("5s")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(3000);
    });

    expect(screen.getByText("8s")).toBeInTheDocument();
  });
});
