import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useTasksSSE } from "@/hooks/useTasksSSE";
import { useTasksStore } from "@/stores/tasks-store";
import type { TaskItem } from "@/types";

function makeTask(overrides: Partial<TaskItem> = {}): TaskItem {
  return {
    task_id: "task-1",
    project_name: "demo",
    task_type: "storyboard",
    media_type: "image",
    resource_id: "segment-1",
    script_file: null,
    payload: {},
    status: "queued",
    result: null,
    error_message: null,
    cancelled_by: null,
    source: "webui",
    queued_at: "2026-02-01T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-02-01T00:00:00Z",
    ...overrides,
  };
}

describe("useTasksSSE (polling)", () => {
  beforeEach(() => {
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("polls on mount, updates store, and cleans up on unmount", async () => {
    const stats = { queued: 1, running: 0, succeeded: 0, failed: 0, total: 1 };
    const listSpy = vi.spyOn(API, "listTasks").mockResolvedValue({
      items: [makeTask()],
      total: 1,
      page: 1,
      page_size: 200,
    });
    const statsSpy = vi.spyOn(API, "getTaskStats").mockResolvedValue(
      { stats } as any,
    );

    const { unmount } = renderHook(() => useTasksSSE("demo"));

    // Flush initial poll (micro-task only, no timer advance)
    await act(async () => {});

    expect(listSpy).toHaveBeenCalledTimes(1);
    expect(statsSpy).toHaveBeenCalledTimes(1);
    expect(useTasksStore.getState().tasks).toHaveLength(1);
    expect(useTasksStore.getState().stats).toEqual(stats);
    expect(useTasksStore.getState().connected).toBe(true);

    // Advance to next poll interval (3s)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(listSpy).toHaveBeenCalledTimes(2);

    unmount();
    expect(useTasksStore.getState().connected).toBe(false);
  });

  it("sets connected=false on fetch error and retries on next interval", async () => {
    const listSpy = vi.spyOn(API, "listTasks").mockRejectedValueOnce(new Error("network"));
    vi.spyOn(API, "getTaskStats").mockRejectedValueOnce(new Error("network"));

    renderHook(() => useTasksSSE("demo"));

    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });

    expect(useTasksStore.getState().connected).toBe(false);

    // Recover on next poll
    listSpy.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 200 });
    vi.spyOn(API, "getTaskStats").mockResolvedValueOnce({ stats: { queued: 0, running: 0, succeeded: 0, failed: 0, total: 0 } } as any);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(useTasksStore.getState().connected).toBe(true);
  });

  it("correctly maps REST 'items' field to store tasks", async () => {
    const task1 = makeTask({ task_id: "t1", status: "queued" });
    const task2 = makeTask({ task_id: "t2", status: "running" });
    vi.spyOn(API, "listTasks").mockResolvedValue({
      items: [task1, task2],
      total: 2,
      page: 1,
      page_size: 200,
    });
    vi.spyOn(API, "getTaskStats").mockResolvedValue({
      stats: { queued: 1, running: 1, succeeded: 0, failed: 0, total: 2 },
    } as any);

    renderHook(() => useTasksSSE("demo"));
    await act(async () => {});

    const { tasks, stats } = useTasksStore.getState();
    expect(tasks).toHaveLength(2);
    expect(tasks[0].task_id).toBe("t1");
    expect(tasks[1].task_id).toBe("t2");
    expect(stats.queued).toBe(1);
    expect(stats.running).toBe(1);
  });

  it("unwraps nested stats from { stats: {...} } envelope", async () => {
    vi.spyOn(API, "listTasks").mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 200,
    });
    // Backend returns { stats: { ... } } wrapper
    vi.spyOn(API, "getTaskStats").mockResolvedValue({
      stats: { queued: 3, running: 2, succeeded: 10, failed: 1, total: 16 },
    } as any);

    renderHook(() => useTasksSSE("demo"));
    await act(async () => {});

    const { stats } = useTasksStore.getState();
    expect(stats).toEqual({ queued: 3, running: 2, succeeded: 10, failed: 1, total: 16 });
  });
});
