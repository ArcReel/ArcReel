import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import {
  useTasksStore,
  useActiveResourceIds,
  selectHasActiveTaskForScriptFile,
} from "@/stores/tasks-store";
import type { GridGeneration } from "@/types/grid";
import type { TaskItem } from "@/types";
import { GridPreviewPanel } from "./GridPreviewPanel";

// useActiveResourceIds 默认包裹真实实现，仅在个别用例里用 mockReturnValue 模拟
// "响应式信号尚未追上真实 store"的场景，验证提交 handler 不依赖它、独立新鲜读 store。
const mockHolder = vi.hoisted(() => ({
  real: undefined as unknown as typeof import("@/stores/tasks-store").useActiveResourceIds,
}));
vi.mock("@/stores/tasks-store", async () => {
  const actual = await vi.importActual<typeof import("@/stores/tasks-store")>("@/stores/tasks-store");
  mockHolder.real = actual.useActiveResourceIds;
  return { ...actual, useActiveResourceIds: vi.fn(actual.useActiveResourceIds) };
});

beforeEach(() => {
  vi.mocked(useActiveResourceIds).mockImplementation(mockHolder.real);
  useTasksStore.setState({ tasks: [], optimisticActive: new Set(), optimisticActiveScriptFile: new Set() });
  useAppStore.setState(useAppStore.getInitialState(), true);
});

function makeGrid(overrides: Partial<GridGeneration> = {}): GridGeneration {
  return {
    id: "grid-1",
    episode: 1,
    script_file: "episode_1.json",
    scene_ids: ["SCN-1"],
    grid_image_path: "grids/grid-1.png",
    rows: 2,
    cols: 2,
    cell_count: 4,
    frame_chain: [],
    status: "completed",
    prompt: null,
    provider: "gemini",
    model: "gemini-image",
    grid_size: "2x2",
    created_at: "2026-07-16T00:00:00Z",
    error_message: null,
    ...overrides,
  };
}

function makeTask(overrides: Partial<TaskItem> = {}): TaskItem {
  return {
    task_id: "t-grid-1",
    project_name: "demo",
    task_type: "grid",
    media_type: "image",
    resource_id: "grid-1",
    resource_type: null,
    script_file: "episode_1.json",
    payload: {},
    status: "running",
    result: null,
    error_message: null,
    cancelled_by: null,
    provider_id: null,
    provider_job_id: null,
    source: "webui",
    queued_at: "2026-07-24T00:00:00Z",
    started_at: "2026-07-24T00:00:00Z",
    finished_at: null,
    updated_at: "2026-07-24T00:00:01Z",
    ...overrides,
  };
}

describe("GridPreviewPanel regenerate", () => {
  it("marks the grid's scriptFile as optimistically active after a successful regenerate submit", async () => {
    useTasksStore.setState({ tasks: [], optimisticActiveScriptFile: new Set() });
    vi.spyOn(API, "getGrid").mockResolvedValue(makeGrid());
    vi.spyOn(API, "regenerateGrid").mockResolvedValue({ success: true, task_id: "t-1", deduped: false });

    render(<GridPreviewPanel projectName="demo" gridIds={["grid-1"]} defaultExpanded />);

    const regenBtn = await screen.findByText("重新生成");
    fireEvent.click(regenBtn);

    await waitFor(() => {
      expect(API.regenerateGrid).toHaveBeenCalledWith("demo", "grid-1");
      const { tasks, optimisticActiveScriptFile } = useTasksStore.getState();
      expect(
        selectHasActiveTaskForScriptFile(tasks, "grid", "episode_1.json", "demo", optimisticActiveScriptFile),
      ).toBe(true);
    });
  });

  it("does not mark occupancy when the regenerate request fails", async () => {
    useTasksStore.setState({ tasks: [], optimisticActiveScriptFile: new Set() });
    vi.spyOn(API, "getGrid").mockResolvedValue(makeGrid());
    vi.spyOn(API, "regenerateGrid").mockRejectedValue(new Error("regen failed"));

    render(<GridPreviewPanel projectName="demo" gridIds={["grid-1"]} defaultExpanded />);

    const regenBtn = await screen.findByText("重新生成");
    fireEvent.click(regenBtn);

    await waitFor(() => {
      expect(API.regenerateGrid).toHaveBeenCalledWith("demo", "grid-1");
    });
    const { tasks, optimisticActiveScriptFile } = useTasksStore.getState();
    expect(
      selectHasActiveTaskForScriptFile(tasks, "grid", "episode_1.json", "demo", optimisticActiveScriptFile),
    ).toBe(false);
  });
});

describe("GridPreviewPanel occupancy", () => {
  it("live tasks store 中任务运行时即使 grid.status 仍为已完成也判定占用，重新生成按钮被禁用", async () => {
    vi.spyOn(API, "getGrid").mockResolvedValue(makeGrid({ status: "completed" }));

    render(<GridPreviewPanel projectName="demo" gridIds={["grid-1"]} defaultExpanded />);

    await screen.findByText("重新生成");

    useTasksStore.setState({ tasks: [makeTask({ status: "running" })] });

    const regenBtn = await screen.findByText("生成中...");
    expect(regenBtn).toBeDisabled();
  });

  it("响应式信号尚未追上真实 store 时，提交仍被 getState() 新鲜读拦截", async () => {
    vi.spyOn(API, "getGrid").mockResolvedValue(makeGrid());
    const regenerateSpy = vi.spyOn(API, "regenerateGrid");
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());

    render(<GridPreviewPanel projectName="demo" gridIds={["grid-1"]} defaultExpanded />);

    const regenBtn = await screen.findByText("重新生成");
    expect(regenBtn).not.toBeDisabled();

    useTasksStore.setState({ tasks: [makeTask({ status: "running" })] });

    fireEvent.click(regenBtn);

    await waitFor(() => {
      expect(pushToast).toHaveBeenCalledWith("该宫格正在生成中，请稍后再试", "error");
    });
    expect(regenerateSpy).not.toHaveBeenCalled();
    // 拒绝提示不得替换面板内容：宫格图与重新生成按钮仍在
    expect(screen.getByText("重新生成")).toBeInTheDocument();
  });
});
