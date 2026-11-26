import { seedTaskState } from "@/test/task-state";
import { beforeEach, describe, it, expect, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { createDeferred } from "@/test/deferred";
import {
  defaultTaskStats,
  mergeTaskSnapshot,
  isActiveStatus,
  isOccupyingStatus,
  isResourceBusy,
  isScriptFileBusy,
  selectLatestTaskByResource,
  selectRecentTasks,
  taskResourceKind,
  useTaskRowsByIds,
  useTasksStore,
} from "./tasks-store";
import type { TaskItem, TaskStatus } from "@/types";

function task(overrides: Partial<TaskItem> & { task_id: string }): TaskItem {
  return {
    project_name: "proj",
    task_type: "reference_video",
    media_type: "video",
    resource_id: "unit-1",
    resource_type: null,
    script_file: null,
    payload: {},
    status: "queued",
    result: null,
    error_message: null,
    cancelled_by: null,
    provider_id: null,
    provider_job_id: null,
    source: "webui",
    queued_at: "2026-07-16T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-07-16T00:00:00Z",
    ...overrides,
  };
}

describe("isActiveStatus", () => {
  it("counts queued and running as active", () => {
    expect(isActiveStatus("queued")).toBe(true);
    expect(isActiveStatus("running")).toBe(true);
  });

  it("counts every other status as inactive", () => {
    const inactive: TaskStatus[] = ["succeeded", "failed", "cancelled"];
    for (const status of inactive) expect(isActiveStatus(status)).toBe(false);
  });
});

describe("isOccupyingStatus", () => {
  it("counts queued/running as occupying", () => {
    expect(isOccupyingStatus("queued")).toBe(true);
    expect(isOccupyingStatus("running")).toBe(true);
  });

  it("counts terminal statuses as not occupying", () => {
    const free: TaskStatus[] = ["succeeded", "failed", "cancelled"];
    for (const status of free) expect(isOccupyingStatus(status)).toBe(false);
  });
});

describe("taskResourceKind", () => {
  it("returns task_type for non-edit tasks", () => {
    expect(taskResourceKind(task({ task_id: "a", task_type: "storyboard" }))).toBe("storyboard");
    expect(taskResourceKind(task({ task_id: "b", task_type: "character" }))).toBe("character");
  });

  it("returns resource_type for image_edit tasks so edits land in the target resource slot", () => {
    expect(
      taskResourceKind(task({ task_id: "a", task_type: "image_edit", resource_type: "character" })),
    ).toBe("character");
    expect(
      taskResourceKind(task({ task_id: "b", task_type: "image_edit", resource_type: "storyboard" })),
    ).toBe("storyboard");
  });

  it("falls back to empty string when an image_edit task has no resource_type", () => {
    expect(taskResourceKind(task({ task_id: "a", task_type: "image_edit", resource_type: null }))).toBe("");
  });
});

describe("selectLatestTaskByResource", () => {
  it("keeps the row with the newest updated_at per resource, ignoring array order", () => {
    const tasks = [
      task({ task_id: "old", resource_id: "unit-1", status: "failed", updated_at: "2026-07-16T00:00:00Z" }),
      task({ task_id: "new", resource_id: "unit-1", status: "running", updated_at: "2026-07-16T01:00:00Z" }),
    ];
    const latest = selectLatestTaskByResource(tasks);
    expect(latest.get("unit-1")?.task_id).toBe("new");
    expect(latest.get("unit-1")?.status).toBe("running");
  });

  it("does not let a stale later-in-array row overwrite a newer one", () => {
    const tasks = [
      task({ task_id: "new", resource_id: "unit-1", updated_at: "2026-07-16T02:00:00Z" }),
      task({ task_id: "old", resource_id: "unit-1", updated_at: "2026-07-16T00:00:00Z" }),
    ];
    expect(selectLatestTaskByResource(tasks).get("unit-1")?.task_id).toBe("new");
  });

  it("filters by projectName and taskType", () => {
    const tasks = [
      task({ task_id: "a", resource_id: "u1", project_name: "p1", task_type: "video" }),
      task({ task_id: "b", resource_id: "u2", project_name: "p2", task_type: "video" }),
      task({ task_id: "c", resource_id: "u3", project_name: "p1", task_type: "storyboard" }),
    ];
    const latest = selectLatestTaskByResource(tasks, { projectName: "p1", taskType: "video" });
    expect([...latest.keys()]).toEqual(["u1"]);
  });

  it("groups distinct resources independently", () => {
    const tasks = [
      task({ task_id: "a", resource_id: "u1" }),
      task({ task_id: "b", resource_id: "u2" }),
    ];
    expect(selectLatestTaskByResource(tasks).size).toBe(2);
  });
});

describe("refreshTasks（多入口共享刷新的在途合并）", () => {
  beforeEach(() => {
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getActiveTasks").mockResolvedValue({ items: [], truncated: false });
  });

  function mockFetch(items: TaskItem[] = []) {
    const listSpy = vi.spyOn(API, "listTasks").mockResolvedValue({
      items,
      total: items.length,
      page: 1,
      page_size: 200,
    });
    vi.spyOn(API, "getTaskStats").mockResolvedValue({
      stats: {
        queued: 0,
        running: 0,
        succeeded: 0,
        failed: 0,
        cancelled: 0,
        total: 0,
      },
    });
    return listSpy;
  }

  it("keeps progress observed after an older snapshot request began", async () => {
    const row = task({ task_id: "late", status: "running" });
    mockFetch([row]);
    useTasksStore.getState().setRefreshScope({ projectName: "proj" });
    seedTaskState({ tasks: [row] });
    const pending = createDeferred<{ items: TaskItem[]; truncated: boolean }>();
    vi.mocked(API.getActiveTasks).mockReturnValueOnce(pending.promise);
    const refresh = useTasksStore.getState().refreshTasks();
    useTasksStore.getState().receiveProgress({ task_id: "late", seq: 2, phase: "waiting", model: "m" });
    pending.resolve({ items: [], truncated: false });
    await refresh;
    expect(useTasksStore.getState().progressById.get("late")?.seq).toBe(2);
  });

  it("作用域未启用时不发请求", async () => {
    const listSpy = mockFetch();

    await useTasksStore.getState().refreshTasks();

    expect(listSpy).not.toHaveBeenCalled();
  });

  it("按作用域拉取并写回 store", async () => {
    const listSpy = mockFetch([task({ task_id: "t1" })]);
    useTasksStore.getState().setRefreshScope({ projectName: "proj" });

    await useTasksStore.getState().refreshTasks();

    expect(listSpy).toHaveBeenCalledWith({ projectName: "proj", pageSize: 200 }, { signal: expect.any(AbortSignal) });
    expect(selectRecentTasks(useTasksStore.getState())).toHaveLength(1);
    expect(useTasksStore.getState().connected).toBe(true);
  });

  it("projectName 为 null 时拉全局任务（不按项目过滤）", async () => {
    const listSpy = mockFetch();
    useTasksStore.getState().setRefreshScope({ projectName: null });

    await useTasksStore.getState().refreshTasks();

    expect(listSpy).toHaveBeenCalledWith({ projectName: undefined, pageSize: 200 }, { signal: expect.any(AbortSignal) });
  });

  it("轮询看到参考生视频任务转成功时失效单元缓存，且不重复失效", async () => {
    useAppStore.setState({ referenceVideoUnitsRevision: 0 });
    useTasksStore.getState().setRefreshScope({ projectName: "proj" });

    mockFetch([task({ task_id: "rv1", task_type: "reference_video", status: "running" })]);
    await useTasksStore.getState().refreshTasks();
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(0);

    vi.restoreAllMocks();
    vi.spyOn(API, "getActiveTasks").mockResolvedValue({ items: [], truncated: false });
    mockFetch([task({ task_id: "rv1", task_type: "reference_video", status: "succeeded" })]);
    await useTasksStore.getState().refreshTasks();
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(1);

    await useTasksStore.getState().refreshTasks();
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(1);
  });

  it("轮询看到参考单元旁白配音任务转成功时失效单元缓存", async () => {
    useAppStore.setState({ referenceVideoUnitsRevision: 0 });
    useTasksStore.getState().setRefreshScope({ projectName: "proj" });

    mockFetch([task({ task_id: "tts1", task_type: "tts", media_type: "audio", status: "running" })]);
    await useTasksStore.getState().refreshTasks();
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(0);

    vi.restoreAllMocks();
    vi.spyOn(API, "getActiveTasks").mockResolvedValue({ items: [], truncated: false });
    mockFetch([task({ task_id: "tts1", task_type: "tts", media_type: "audio", status: "succeeded" })]);
    await useTasksStore.getState().refreshTasks();
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(1);
  });

  it("整个生命周期落在两次轮询之间的参考生视频任务也算完成", async () => {
    useAppStore.setState({ referenceVideoUnitsRevision: 0 });
    useTasksStore.getState().setRefreshScope({ projectName: "proj" });

    mockFetch([task({ task_id: "other", task_type: "storyboard", status: "succeeded" })]);
    await useTasksStore.getState().refreshTasks();
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(0);

    vi.restoreAllMocks();
    vi.spyOn(API, "getActiveTasks").mockResolvedValue({ items: [], truncated: false });
    mockFetch([
      task({ task_id: "other", task_type: "storyboard", status: "succeeded" }),
      task({ task_id: "rv-fast", task_type: "reference_video", status: "succeeded" }),
    ]);
    await useTasksStore.getState().refreshTasks();
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(1);
  });

  it("切作用域后的第一轮只建基线，不把新作用域的历史成功任务当作刚完成", async () => {
    useAppStore.setState({ referenceVideoUnitsRevision: 0 });
    useTasksStore.getState().setRefreshScope({ projectName: "proj" });
    mockFetch([]);
    await useTasksStore.getState().refreshTasks();

    vi.restoreAllMocks();
    vi.spyOn(API, "getActiveTasks").mockResolvedValue({ items: [], truncated: false });
    useTasksStore.getState().setRefreshScope({ projectName: "other-proj" });
    mockFetch([task({ task_id: "rv-old", task_type: "reference_video", status: "succeeded" })]);
    await useTasksStore.getState().refreshTasks();

    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(0);
  });

  it("首轮拉取不把历史成功的参考生视频任务当作刚完成", async () => {
    useAppStore.setState({ referenceVideoUnitsRevision: 0 });
    useTasksStore.getState().setRefreshScope({ projectName: "proj" });
    mockFetch([task({ task_id: "rv-old", task_type: "reference_video", status: "succeeded" })]);

    await useTasksStore.getState().refreshTasks();

    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(0);
  });

  it("在途期间到达的多次调用合并为结束后再跑一轮", async () => {
    const releases: Array<() => void> = [];
    const listSpy = vi.spyOn(API, "listTasks").mockImplementation(
      () =>
        new Promise((resolve) => {
          releases.push(() => resolve({ items: [], total: 0, page: 1, page_size: 200 }));
        }),
    );
    vi.spyOn(API, "getTaskStats").mockResolvedValue({
      stats: {
        queued: 0,
        running: 0,
        succeeded: 0,
        failed: 0,
        cancelled: 0,
        total: 0,
      },
    });
    useTasksStore.getState().setRefreshScope({ projectName: "proj" });

    const first = useTasksStore.getState().refreshTasks();
    const second = useTasksStore.getState().refreshTasks();
    const third = useTasksStore.getState().refreshTasks();

    expect(listSpy).toHaveBeenCalledTimes(1);

    releases[0]();
    await vi.waitFor(() => expect(releases).toHaveLength(2));
    releases[1]();
    await Promise.all([first, second, third]);

    expect(listSpy).toHaveBeenCalledTimes(2);
  });

  it("落定时作用域已切换的迟到响应不写回", async () => {
    const releases: Array<(items: TaskItem[]) => void> = [];
    vi.spyOn(API, "listTasks").mockImplementation(
      () =>
        new Promise((resolve) => {
          releases.push((items) => resolve({ items, total: items.length, page: 1, page_size: 200 }));
        }),
    );
    vi.spyOn(API, "getTaskStats").mockResolvedValue({
      stats: {
        queued: 0,
        running: 0,
        succeeded: 0,
        failed: 0,
        cancelled: 0,
        total: 0,
      },
    });
    useTasksStore.getState().setRefreshScope({ projectName: "old-project" });

    const pending = useTasksStore.getState().refreshTasks();
    await Promise.resolve();
    useTasksStore.getState().setRefreshScope({ projectName: "new-project" });
    releases[0]([task({ task_id: "stale" })]);
    await pending;

    expect(selectRecentTasks(useTasksStore.getState())).toEqual([]);
  });

  it("请求失败时置 connected=false 并留旧数据", async () => {
    mockFetch([task({ task_id: "t1" })]);
    useTasksStore.getState().setRefreshScope({ projectName: "proj" });
    await useTasksStore.getState().refreshTasks();

    vi.spyOn(API, "listTasks").mockRejectedValue(new Error("network"));
    await useTasksStore.getState().refreshTasks();

    expect(useTasksStore.getState().connected).toBe(false);
    expect(selectRecentTasks(useTasksStore.getState())).toHaveLength(1);
  });

});

describe("active snapshot reconciliation", () => {
  const state = useTasksStore.getState;
  const active = (items: TaskItem[], truncated = false) => ({ items, truncated });
  const page = (items: TaskItem[]) => ({ items, page: 1, page_size: 200, total: items.length });
  const row = task({ task_id: "B", status: "running" });
  const busy = () => isResourceBusy("reference_video", "proj", row.resource_id);

  beforeEach(() => {
    state().setRefreshScope(null);
    state().setRefreshScope({ projectName: "proj" });
    vi.spyOn(API, "getActiveTasks").mockResolvedValue(active([]));
    vi.spyOn(API, "listTasks").mockResolvedValue(page([]));
    vi.spyOn(API, "getTaskStats").mockResolvedValue({ stats: defaultTaskStats });
  });

  it("applies progress that overtakes the first active snapshot only after running is confirmed", async () => {
    const progress = { task_id: row.task_id, seq: 1, phase: "waiting" as const, model: "m" };
    vi.mocked(API.getActiveTasks).mockResolvedValueOnce(active([{ ...row, progress }]));
    const refreshed = state().refreshTasks();
    state().receiveProgress({ ...progress, seq: 3 });
    state().receiveProgress({ ...progress, seq: 2 });
    expect(state().progressById.size).toBe(0);
    await refreshed;
    expect(state().progressById.get(row.task_id)?.seq).toBe(3);
  });

  it("releases a stalled snapshot refresh and lets the next refresh reconnect", async () => {
    await state().refreshTasks();
    expect(state().connected).toBe(true);
    vi.mocked(API.getActiveTasks).mockImplementationOnce((_project, options) =>
      new Promise((_resolve, reject) => options?.signal?.addEventListener("abort", () => reject(options.signal?.reason))),
    );

    vi.useFakeTimers();
    try {
      const pending = state().refreshTasks();
      await vi.runOnlyPendingTimersAsync();
      expect(state().connected).toBe(false);
      await pending;
      vi.mocked(API.getActiveTasks).mockResolvedValueOnce(active([row]));
      await state().refreshTasks();
      expect(state().connected).toBe(true);
      expect(busy()).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it.each(["listTasks", "getTaskStats"] as const)("settles retained tasks when %s fails", async (request) => {
    const release = state().retainTask("proj", row.task_id);
    vi.mocked(API[request]).mockRejectedValueOnce(new Error("unavailable"));
    vi.spyOn(API, "lookupTasks").mockResolvedValue({
      items: [{ ...row, status: "succeeded" }],
      missing_ids: [],
    });

    await state().refreshTasks();

    expect(state().connected).toBe(false);
    expect(state().tracked.get(row.task_id)?.status).toBe("resolved");
    expect(state().rowsById.get(row.task_id)?.status).toBe("succeeded");
    release();
  });

  it.each(["reference_video", "tts"] as const)("invalidates units when an off-page %s submission succeeds", async (type) => {
    const initialRevision = useAppStore.getState().referenceVideoUnitsRevision;
    await state().refreshTasks();
    const submission = state().beginOptimisticActive("proj", type, row.resource_id, type);
    submission.settle([row.task_id]);
    vi.spyOn(API, "lookupTasks").mockResolvedValue({
      items: [{ ...row, task_type: type, status: "succeeded" }],
      missing_ids: [],
    });

    await state().refreshTasks();

    expect(isResourceBusy(type, "proj", row.resource_id)).toBe(false);
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(initialRevision + 1);
    await state().refreshTasks();
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(initialRevision + 1);
  });

  it("accepts a retry within the same millisecond and ignores the older failure in recent history", async () => {
    const failed = { ...row, status: "failed" as const, updated_at: "2026-07-16T08:00:00.100001+08:00" };
    vi.mocked(API.listTasks).mockResolvedValue(page([failed]));
    await state().refreshTasks();
    expect(busy()).toBe(false);
    vi.mocked(API.getActiveTasks).mockResolvedValue(active([{ ...row, updated_at: "2026-07-16T00:00:00.100002Z" }]));
    await state().refreshTasks();
    expect(selectRecentTasks(state())[0].status).toBe("running");
    expect(busy()).toBe(true);
  });

  it("retains known occupancy across truncated snapshots and releases it on a complete snapshot", async () => {
    vi.mocked(API.getActiveTasks).mockResolvedValueOnce(active([row], true)).mockResolvedValueOnce(active([], true));
    await state().refreshTasks();
    expect(busy()).toBe(true);
    expect(isResourceBusy("reference_video", "proj", "other")).toBe(false);
    await state().refreshTasks();
    expect(busy()).toBe(true);
    await state().refreshTasks();
    expect(busy()).toBe(false);
  });

  it("hands a script mark to a missing member and a live member, retaining the latter to completion", async () => {
    const scriptFile = "scripts/episode.json";
    const grid = { ...row, task_type: "grid", script_file: scriptFile };
    const leaseA = state().retainTask("proj", "A");
    const release = state().retainTask("proj", "B");
    const releaseSecond = state().retainTask("proj", "B");
    state().beginOptimisticActiveForScriptFile("proj", "grid", scriptFile).settle(["A", "B"]);
    expect(isScriptFileBusy("grid", "episode.json", "proj")).toBe(true);
    const lookup = vi.spyOn(API, "lookupTasks").mockRejectedValueOnce(new Error("unavailable"))
      .mockResolvedValueOnce({ items: [grid], missing_ids: ["A"] })
      .mockResolvedValueOnce({ items: [{ ...grid, status: "succeeded" }], missing_ids: [] });
    await state().refreshTasks();
    expect(state().tracked.get("A")?.status).toBe("pending");
    expect(isScriptFileBusy("grid", "episode.json", "proj")).toBe(true);
    await state().refreshTasks();
    expect(isScriptFileBusy("grid", "episode.json", "proj")).toBe(true);
    expect(state().tracked.get("A")?.status).toBe("missing");
    releaseSecond();
    expect(state().tracked.get("B")?.retainCount).toBe(1);
    await state().refreshTasks();
    expect(lookup.mock.calls.at(-1)?.[0]).toEqual(["B"]);
    expect(state().tracked.get("B")?.status).toBe("resolved");
    expect(isScriptFileBusy("grid", "episode.json", "proj")).toBe(false);
    release();
    leaseA();
  });

  it("retries timed-out members on the next refresh", async () => {
    const started = createDeferred<void>();
    vi.spyOn(API, "lookupTasks").mockImplementationOnce((_ids, _project, signal) => {
      started.resolve();
      return new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(signal.reason)));
    }).mockResolvedValueOnce({ items: [], missing_ids: ["B"] });
    state().retainTask("proj", "B");
    vi.useFakeTimers();
    try {
      const pending = state().refreshTasks();
      await started.promise;
      await vi.runOnlyPendingTimersAsync();
      await pending;
      expect(state().tracked.get("B")?.status).toBe("pending");
      await state().refreshTasks();
      expect(state().tracked.get("B")?.status).toBe("missing");
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps another generation occupying the same resource after an edit finishes", async () => {
    const generation = { ...row, task_type: "character" };
    const edit = { ...row, task_id: "edit", task_type: "image_edit", resource_type: "character" };
    vi.mocked(API.getActiveTasks).mockResolvedValueOnce(active([generation, edit])).mockResolvedValueOnce(active([generation]));
    await state().refreshTasks();
    vi.mocked(API.listTasks).mockResolvedValue(page([{ ...edit, status: "succeeded" }]));
    await state().refreshTasks();
    expect(isResourceBusy("character", "proj", row.resource_id)).toBe(true);
    expect(isResourceBusy("character", "other", row.resource_id)).toBe(false);
  });

  it("keeps an already handed-off task when an earlier snapshot arrives", async () => {
    vi.mocked(API.getActiveTasks).mockResolvedValueOnce(active([row]));
    await state().refreshTasks();
    const response = createDeferred<ReturnType<typeof active>>();
    vi.mocked(API.getActiveTasks).mockReturnValueOnce(response.promise);
    const pending = state().refreshTasks();
    state().beginOptimisticActive("proj", "reference_video", row.resource_id, "reference_video").settle(["B"]);
    response.resolve(active([]));
    await pending;
    expect(busy()).toBe(true);
    await state().refreshTasks();
    expect(busy()).toBe(false);
  });

  it("releases only its own empty submission, and batches outstanding IDs by 200", async () => {
    const mark = () => state().beginOptimisticActive("proj", "reference_video", row.resource_id, "reference_video");
    const first = mark();
    mark().settle([]);
    expect(busy()).toBe(true);
    first.rollback();
    expect(busy()).toBe(false);
    const ids = Array.from({length: 201}, (_, index) => String(index));
    mark().settle(ids);
    const lookup = vi.spyOn(API, "lookupTasks").mockImplementation(async (chunk) => ({ items: [], missing_ids: chunk }));
    await state().refreshTasks();
    expect(lookup.mock.calls.map(([chunk]) => chunk.length)).toEqual([200, 1]);
    expect(busy()).toBe(false);
  });

  it("discards late lookup results across A to B to A and deactivation", async () => {
    const started = createDeferred<void>();
    const result = createDeferred<Awaited<ReturnType<typeof API.lookupTasks>>>();
    vi.spyOn(API, "lookupTasks").mockImplementationOnce(() => { started.resolve(); return result.promise; });
    state().retainTask("proj", "B");
    const old = state().refreshTasks();
    await started.promise;
    state().setRefreshScope({ projectName: "other" });
    state().setRefreshScope({ projectName: "proj" });
    result.resolve({ items: [row], missing_ids: [] });
    await old;
    expect(busy()).toBe(false);
    expect(state().tracked.size).toBe(0);
    state().beginOptimisticActive("proj", "reference_video", row.resource_id, "reference_video");
    state().setRefreshScope(null);
    expect(busy()).toBe(false);
  });

  it("merges the cancelled and skipped rows even when a stale active response follows", async () => {
    const initialRevision = useAppStore.getState().referenceVideoUnitsRevision;
    const other = { ...row, task_id: "C", status: "queued" as const };
    vi.mocked(API.listTasks).mockResolvedValue(page([other, row]));
    vi.mocked(API.getActiveTasks).mockResolvedValue(active([other, row]));
    await state().refreshTasks();
    const response = createDeferred<ReturnType<typeof active>>();
    vi.mocked(API.getActiveTasks).mockReturnValueOnce(response.promise);
    const pending = state().refreshTasks();
    vi.spyOn(API, "cancelTask").mockResolvedValue({
      cancelled: [{ ...other, status: "cancelled" }], skipped_terminal: [{ ...row, status: "succeeded" }],
    });
    await state().cancelTask("C");
    response.resolve(active([other, row]));
    await pending;
    expect(selectRecentTasks(state()).map((t) => t.status)).toEqual(["cancelled", "succeeded"]);
    expect(busy()).toBe(false);
    expect(useAppStore.getState().referenceVideoUnitsRevision).toBe(initialRevision + 1);
  });
});

describe("useTaskRowsByIds", () => {
  beforeEach(() => {
    useTasksStore.setState(useTasksStore.getInitialState(), true);
  });

  it("任务字段变化时向订阅方推送新读数", () => {
    const ids = ["t-1"];
    const { result } = renderHook(() => useTaskRowsByIds(ids));

    act(() => {
      seedTaskState({ tasks: [task({ task_id: "t-1", status: "running" })] });
    });
    expect(result.current.get("t-1")?.status).toBe("running");

    act(() => {
      seedTaskState({ tasks: [task({ task_id: "t-1", status: "succeeded" })] });
    });
    expect(result.current.get("t-1")?.status).toBe("succeeded");
  });

  it("无关任务变化时不产生新的读数对象", () => {
    const watched = task({ task_id: "t-1" });
    seedTaskState({ tasks: [watched, task({ task_id: "t-2" })] });
    const ids = ["t-1"];
    const { result } = renderHook(() => useTaskRowsByIds(ids));
    const first = result.current;

    act(() => {
      seedTaskState({
        tasks: [watched, task({ task_id: "t-2", status: "succeeded" })],
      });
    });
    expect(result.current).toBe(first);
  });
});

describe("text task progress", () => {
  it("retains the maximum sequence across snapshots and events until a terminal row wins", () => {
    const row = task({ task_id: "text", status: "running" });
    const progress = (seq: number) => ({ task_id: "text", seq, phase: "waiting" as const, model: "model" });
    const store = useTasksStore;
    for (const first of [1, 2, 3]) {
      for (const second of [1, 2, 3]) {
        for (const third of [1, 2, 3]) {
          store.getState().setRefreshScope(null);
          seedTaskState({ tasks: [row] });
          store.getState().receiveProgress(progress(first));
          store.setState(mergeTaskSnapshot(store.getState(), {
            items: [{ ...row, progress: progress(second) }], truncated: false,
          }, [row]));
          store.getState().receiveProgress(progress(third));
          expect(store.getState().progressById.get("text")?.seq).toBe(Math.max(first, second, third));
          seedTaskState({ tasks: [{ ...row, status: "failed" }] });
          seedTaskState({ tasks: [{ ...row, progress: progress(6) }] });
          store.getState().receiveProgress(progress(7));
          expect(store.getState().progressById.size).toBe(0);
        }
      }
    }
  });
});
