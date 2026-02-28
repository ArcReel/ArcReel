import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API, type TaskStreamOptions } from "@/api";
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
    source: "webui",
    queued_at: "2026-02-01T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-02-01T00:00:00Z",
    ...overrides,
  };
}

describe("useTasksSSE", () => {
  beforeEach(() => {
    useTasksStore.setState(useTasksStore.getInitialState(), true);
  });

  it("connects, applies snapshot/task/heartbeat events, and cleans up on unmount", () => {
    const captured: TaskStreamOptions[] = [];
    const source = { close: vi.fn() } as unknown as EventSource;

    const openSpy = vi.spyOn(API, "openTaskStream").mockImplementation((options) => {
      captured.push(options ?? {});
      return source;
    });

    const { unmount } = renderHook(() => useTasksSSE("demo"));
    expect(openSpy).toHaveBeenCalledTimes(1);
    expect(captured[0].projectName).toBe("demo");

    const stats = { queued: 1, running: 0, succeeded: 0, failed: 0, total: 1 };
    act(() => {
      captured[0].onSnapshot?.(
        { tasks: [makeTask()], stats },
        new MessageEvent("snapshot"),
      );
    });
    expect(useTasksStore.getState().tasks).toHaveLength(1);
    expect(useTasksStore.getState().connected).toBe(true);

    act(() => {
      captured[0].onTask?.(
        {
          action: "updated",
          task: makeTask({ status: "running" }),
          stats: { queued: 0, running: 1, succeeded: 0, failed: 0, total: 1 },
        },
        new MessageEvent("task"),
      );
    });
    expect(useTasksStore.getState().tasks[0].status).toBe("running");
    expect(useTasksStore.getState().stats.running).toBe(1);

    act(() => {
      captured[0].onHeartbeat?.(
        { last_event_id: 42, generated_at: "2026-02-02T00:00:00Z" },
        new MessageEvent("heartbeat"),
      );
    });
    expect(useTasksStore.getState().connected).toBe(true);

    unmount();
    expect(source.close).toHaveBeenCalledTimes(1);
    expect(useTasksStore.getState().connected).toBe(false);
  });

  it("closes and reconnects after onError", () => {
    vi.useFakeTimers();

    const firstSource = { close: vi.fn() } as unknown as EventSource;
    const secondSource = { close: vi.fn() } as unknown as EventSource;
    const captured: TaskStreamOptions[] = [];
    let connectCount = 0;

    vi.spyOn(API, "openTaskStream").mockImplementation((options) => {
      captured.push(options ?? {});
      connectCount += 1;
      return connectCount === 1 ? firstSource : secondSource;
    });

    const { unmount } = renderHook(() => useTasksSSE("demo"));
    expect(connectCount).toBe(1);

    act(() => {
      captured[0].onError?.(new Event("error"));
    });
    expect(useTasksStore.getState().connected).toBe(false);
    expect((firstSource.close as unknown as ReturnType<typeof vi.fn>)).toHaveBeenCalledTimes(1);

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(connectCount).toBe(2);

    unmount();
    expect((secondSource.close as unknown as ReturnType<typeof vi.fn>)).toHaveBeenCalledTimes(1);
  });
});
