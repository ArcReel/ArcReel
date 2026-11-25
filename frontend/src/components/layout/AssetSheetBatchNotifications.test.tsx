import { seedTaskState } from "@/test/task-state";
import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { TaskFailureListener } from "@/components/layout/TaskFailureListener";
import { defaultTaskStats, useTasksStore } from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";
import { suppressedAssetSheetTaskIds, useAssetSheetBatchStore } from "@/stores/asset-sheet-batch-store";
import { makeTask } from "@/test/factories";
import type { AssetSheetBatchMember } from "@/types";

function member(overrides: Partial<AssetSheetBatchMember>): AssetSheetBatchMember {
  return {
    unit_id: "character/Alice",
    asset_type: "character",
    name: "Alice",
    derivative: null,
    task_id: "t1",
    deduped: false,
    status: "queued",
    ...overrides,
  };
}

const OWNER = { task_id: "t1", project_name: "demo", task_type: "character", media_type: "image", resource_id: "Alice" } as const;
const DERIVATIVE = {
  task_id: "t2",
  project_name: "demo",
  task_type: "character_derivative",
  media_type: "image",
  resource_id: "Alice/战损",
} as const;

afterEach(() => {
  useTasksStore.getState().setRefreshScope(null);
  vi.restoreAllMocks();
});

beforeEach(() => {
  useAppStore.setState(useAppStore.getInitialState(), true);
  useAssetSheetBatchStore.setState(useAssetSheetBatchStore.getInitialState(), true);
  useTasksStore.getState().setRefreshScope({ projectName: "demo" });
  seedTaskState({ tasks: [], connected: true });
  useAssetSheetBatchStore.getState().track({
    batchId: "b1",
    projectName: "demo",
    members: [member({}), member({ unit_id: "character/Alice/战损", derivative: "战损", task_id: "t2" })],
  });
});

describe("AssetSheetBatchNotifications", () => {
  it("suppresses a failed evicted member settled via lookup when it later re-enters the recent list", async () => {
    const owner = makeTask({ ...OWNER, status: "running" });
    const derivative = makeTask({ ...DERIVATIVE, status: "running" });
    const history = Array.from({ length: 200 }, (_, index) => makeTask({
      task_id: `history-${index}`, project_name: "demo", status: "succeeded",
    }));
    const active = vi.spyOn(API, "getActiveTasks").mockResolvedValue({ items: [owner, derivative], truncated: false });
    const recent = vi.spyOn(API, "listTasks").mockResolvedValue({ items: history, total: 202, page: 1, page_size: 200 });
    vi.spyOn(API, "getTaskStats").mockResolvedValue({ stats: defaultTaskStats });
    const lookup = vi.spyOn(API, "lookupTasks").mockResolvedValue({ items: [], missing_ids: [] });
    const { refreshTasks } = useTasksStore.getState();

    render(<TaskFailureListener projectName="demo" />);
    await act(refreshTasks);

    const failedOwner = { ...owner, status: "failed" as const, error_message: "单张生成失败", updated_at: "2026-07-16T00:01:00Z" };
    const succeededDerivative = { ...derivative, status: "succeeded" as const, updated_at: "2026-07-16T00:01:00Z" };
    active.mockResolvedValue({ items: [], truncated: false });
    lookup.mockResolvedValue({ items: [failedOwner, succeededDerivative], missing_ids: [] });
    await act(refreshTasks);

    expect(useAppStore.getState().workspaceNotifications).toHaveLength(1);
    expect(useAppStore.getState().workspaceNotifications[0]?.text).toContain("1 张已生成，1 张失败");
    expect(suppressedAssetSheetTaskIds("demo")).toEqual(new Set([owner.task_id]));

    recent.mockResolvedValue({ items: [failedOwner, ...history.slice(0, 199)], total: 202, page: 1, page_size: 200 });
    await act(refreshTasks);
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(1);
    expect(suppressedAssetSheetTaskIds("demo")).toEqual(new Set([owner.task_id]));

    recent.mockResolvedValue({ items: history, total: 200, page: 1, page_size: 200 });
    await act(refreshTasks);
    expect(suppressedAssetSheetTaskIds("demo").size).toBe(0);
  });

  it("restores an unfinished batch on return and reports evicted, failed and missing members once", async () => {
    const owner = makeTask({ ...OWNER, status: "running" });
    const derivative = makeTask({ ...DERIVATIVE, status: "queued" });
    const third = member({ unit_id: "scene/庭院", asset_type: "scene", name: "庭院", task_id: "t3" });
    const batch = useAssetSheetBatchStore.getState().batches.b1!;
    useAssetSheetBatchStore.getState().track({ ...batch, members: [...batch.members, third] });

    const recent = vi.spyOn(API, "listTasks");
    const history = Array.from({ length: 199 }, (_, index) => makeTask({
      task_id: `history-${index}`, project_name: "demo", status: "succeeded",
    }));
    recent.mockResolvedValue({ items: [owner, derivative], total: 2, page: 1, page_size: 200 });
    vi.spyOn(API, "getActiveTasks").mockResolvedValue({ items: [], truncated: false });
    vi.spyOn(API, "getTaskStats").mockResolvedValue({ stats: defaultTaskStats });
    const lookup = vi.spyOn(API, "lookupTasks").mockResolvedValue({ items: [], missing_ids: [] });
    const { refreshTasks, setRefreshScope } = useTasksStore.getState();
    const { rerender } = render(<TaskFailureListener projectName="demo" />);
    await act(refreshTasks);

    act(() => setRefreshScope({ projectName: "other" }));
    rerender(<TaskFailureListener projectName="other" />);
    expect(useTasksStore.getState().tracked.has(owner.task_id)).toBe(false);
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);

    act(() => setRefreshScope({ projectName: "demo" }));
    rerender(<TaskFailureListener projectName="demo" />);
    await act(refreshTasks);
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);

    const finishedOwner = { ...owner, status: "failed" as const, error_message: "单张生成失败" };
    recent.mockResolvedValue({ items: [finishedOwner, ...history], total: 202, page: 1, page_size: 200 });
    lookup.mockResolvedValue({
      items: [{ ...derivative, status: "succeeded" }],
      missing_ids: ["t3"],
    });
    await act(refreshTasks);
    await act(refreshTasks);

    const notifications = useAppStore.getState().workspaceNotifications;
    expect(notifications).toHaveLength(1);
    expect(notifications[0]).toMatchObject({
      tone: "error",
      target: { type: "character", id: "Alice" },
    });
    expect(notifications[0]?.text).toContain("1 张已生成，1 张失败，1 张结果不可用");
    expect(useAssetSheetBatchStore.getState().batches).toEqual({});
    expect(useTasksStore.getState().tracked.size).toBe(0);
    expect(suppressedAssetSheetTaskIds("demo")).toEqual(new Set([owner.task_id]));
    recent.mockResolvedValue({ items: history, total: 199, page: 1, page_size: 200 });
    await act(refreshTasks);
    expect(suppressedAssetSheetTaskIds("demo").size).toBe(0);
  });

  it("sums up a failed batch in one locatable notification instead of one per task", async () => {
    seedTaskState({
      tasks: [makeTask({ ...OWNER, status: "running" }), makeTask({ ...DERIVATIVE, status: "queued" })],
    });
    render(<TaskFailureListener projectName="demo" />);

    act(() => {
      seedTaskState({
        tasks: [
          makeTask({ ...OWNER, status: "failed" }),
          makeTask({ ...DERIVATIVE, status: "failed", error_code: "cascade_blocked_dependency" }),
        ],
      });
    });

    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    const [notification] = useAppStore.getState().workspaceNotifications;
    expect(notification.tone).toBe("error");
    expect(notification.text).toContain("0 张已生成，2 张失败");
    expect(notification.text).toContain("1 张衍生图因本体资产图未生成而未提交");
    expect(notification.target).toMatchObject({ type: "character", id: "Alice" });
    expect(useAssetSheetBatchStore.getState().batches).toEqual({});
  });

  it("waits for every member before reporting a successful batch", async () => {
    seedTaskState({
      tasks: [makeTask({ ...OWNER, status: "succeeded" }), makeTask({ ...DERIVATIVE, status: "running" })],
    });
    render(<TaskFailureListener projectName="demo" />);
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);

    act(() => {
      seedTaskState({
        tasks: [makeTask({ ...OWNER, status: "succeeded" }), makeTask({ ...DERIVATIVE, status: "succeeded" })],
      });
    });

    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    expect(useAppStore.getState().workspaceNotifications[0]).toMatchObject({
      tone: "success",
      text: "资产图批量生成完成：2 张已生成",
    });
  });

  it("counts results whose asset was deleted during generation apart from generated sheets", async () => {
    seedTaskState({
      tasks: [makeTask({ ...OWNER, status: "running" }), makeTask({ ...DERIVATIVE, status: "running" })],
    });
    render(<TaskFailureListener projectName="demo" />);

    act(() => {
      seedTaskState({
        tasks: [
          makeTask({ ...OWNER, status: "succeeded" }),
          makeTask({ ...DERIVATIVE, status: "succeeded", result: { target_deleted: true, trash_record_id: "r1" } }),
        ],
      });
    });

    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    expect(useAppStore.getState().workspaceNotifications[0]).toMatchObject({
      tone: "warning",
      text: "资产图批量生成完成：1 张已生成；其中 1 张生成完成时资产已删除，结果已移入项目回收站",
    });
  });

  it.each([true, false])("reports submission failures alongside queued members: queued=%s", async (queued) => {
    useAssetSheetBatchStore.setState(useAssetSheetBatchStore.getInitialState(), true);
    useAssetSheetBatchStore.getState().track({
      batchId: "submission-failed",
      projectName: "demo",
      members: [
        member({ task_id: queued ? "t1" : null, status: queued ? "queued" : "failed" }),
        member({ unit_id: "character/Alice/战损", derivative: "战损", task_id: null, status: "failed", problem_code: "generation_dependency_failed" }),
      ],
    });
    if (queued) seedTaskState({ tasks: [makeTask({ ...OWNER, status: "running" })] });
    render(<TaskFailureListener projectName="demo" />);
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(queued ? 0 : 1);
    if (queued) {
      act(() => seedTaskState({ tasks: [makeTask({ ...OWNER, status: "succeeded" })] }));
    }

    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    const [notification] = useAppStore.getState().workspaceNotifications;
    expect(notification.tone).toBe("error");
    expect(notification.text).toContain(queued ? "1 张已生成，1 张失败" : "0 张已生成，2 张失败");
    expect(notification.text).toContain("1 张衍生图因本体资产图未生成而未提交");
    expect(notification.target).toMatchObject({ type: "character", id: "Alice" });
    expect(useAssetSheetBatchStore.getState().batches).toEqual({});
  });
});
