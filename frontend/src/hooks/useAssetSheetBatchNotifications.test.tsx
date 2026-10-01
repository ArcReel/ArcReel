import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAssetSheetBatchNotifications } from "@/hooks/useAssetSheetBatchNotifications";
import { useTaskFailureNotifications } from "@/hooks/useTaskFailureNotifications";
import { useAppStore } from "@/stores/app-store";
import { useAssetSheetBatchStore } from "@/stores/asset-sheet-batch-store";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";
import type { AssetSheetBatchMember } from "@/types";

function Harness() {
  useTaskFailureNotifications("demo");
  useAssetSheetBatchNotifications("demo");
  return null;
}

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

afterEach(() => vi.restoreAllMocks());

beforeEach(() => {
  useAppStore.setState(useAppStore.getInitialState(), true);
  useAssetSheetBatchStore.setState({ batches: {}, trackedTaskIds: new Set() });
  useTasksStore.setState({ tasks: [], connected: true });
  useAssetSheetBatchStore.getState().track({
    batchId: "b1",
    projectName: "demo",
    members: [member({}), member({ unit_id: "character/Alice/战损", derivative: "战损", task_id: "t2" })],
  });
});

describe("useAssetSheetBatchNotifications", () => {
  it("includes a member outside the paginated task list in the batch result", async () => {
    useTasksStore.setState({ tasks: [makeTask({ ...OWNER, status: "succeeded" })] });
    vi.spyOn(API, "getTask").mockResolvedValue(
      makeTask({ ...DERIVATIVE, status: "failed", error_code: "cascade_blocked_dependency" }),
    );

    render(<Harness />);

    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    const [notification] = useAppStore.getState().workspaceNotifications;
    expect(notification.tone).toBe("error");
    expect(notification.text).toContain("1 张已生成，1 张失败");
    expect(notification.target).toMatchObject({ type: "character", id: "Alice" });
    expect(useAssetSheetBatchStore.getState().batches).toEqual({});
  });

  it("sums up a failed batch in one locatable notification instead of one per task", async () => {
    useTasksStore.setState({
      tasks: [makeTask({ ...OWNER, status: "running" }), makeTask({ ...DERIVATIVE, status: "queued" })],
    });
    render(<Harness />);

    act(() => {
      useTasksStore.setState({
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
    useTasksStore.setState({
      tasks: [makeTask({ ...OWNER, status: "succeeded" }), makeTask({ ...DERIVATIVE, status: "running" })],
    });
    render(<Harness />);
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);

    act(() => {
      useTasksStore.setState({
        tasks: [makeTask({ ...OWNER, status: "succeeded" }), makeTask({ ...DERIVATIVE, status: "succeeded" })],
      });
    });

    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    expect(useAppStore.getState().workspaceNotifications[0]).toMatchObject({
      tone: "success",
      text: "资产图批量生成完成：2 张已生成",
    });
  });

  it.each([true, false])("reports submission failures alongside queued members: queued=%s", async (queued) => {
    useAssetSheetBatchStore.setState({ batches: {}, trackedTaskIds: new Set() });
    useAssetSheetBatchStore.getState().track({
      batchId: "submission-failed",
      projectName: "demo",
      members: [
        member({ task_id: queued ? "t1" : null, status: queued ? "queued" : "failed" }),
        member({ unit_id: "character/Alice/战损", derivative: "战损", task_id: null, status: "failed", problem_code: "generation_dependency_failed" }),
      ],
    });
    if (queued) useTasksStore.setState({ tasks: [makeTask({ ...OWNER, status: "running" })] });
    render(<Harness />);
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(queued ? 0 : 1);
    if (queued) {
      act(() => useTasksStore.setState({ tasks: [makeTask({ ...OWNER, status: "succeeded" })] }));
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
