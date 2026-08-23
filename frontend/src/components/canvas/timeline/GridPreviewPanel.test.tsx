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
    split_at: "2026-07-16T00:10:00Z",
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
    expect(regenBtn).toBeEnabled();

    useTasksStore.setState({ tasks: [makeTask({ status: "running" })] });

    fireEvent.click(regenBtn);

    await waitFor(() => {
      expect(pushToast).toHaveBeenCalledWith("该多宫格分镜正在生成中，请稍后再试", "error");
    });
    expect(regenerateSpy).not.toHaveBeenCalled();
    // 拒绝提示不得替换面板内容：宫格图与重新生成按钮仍在
    expect(screen.getByText("重新生成")).toBeInTheDocument();
  });
});

describe("GridPreviewPanel split", () => {
  it("切分成功后应用指纹、更新 split_at 并弹成功提示；跳过的分镜另弹警示", async () => {
    vi.spyOn(API, "getGrid").mockResolvedValue(makeGrid({ split_at: null }));
    const { useProjectsStore } = await import("@/stores/projects-store");
    vi.spyOn(API, "splitGrid").mockResolvedValue({
      success: true,
      split_at: "2026-07-16T01:00:00Z",
      updated_scene_ids: ["SCN-1", "SCN-2"],
      missing_scene_ids: ["SCN-9"],
      asset_fingerprints: { "storyboards/scene_SCN-1.png": 42 },
    });

    render(<GridPreviewPanel projectName="demo" gridIds={["grid-1"]} defaultExpanded />);

    const splitBtn = await screen.findByText("切分落格");
    fireEvent.click(splitBtn);

    await waitFor(() => {
      expect(API.splitGrid).toHaveBeenCalledWith("demo", "grid-1");
      expect(useProjectsStore.getState().assetFingerprints["storyboards/scene_SCN-1.png"]).toBe(42);
      // 单槽 toast：有跳过分镜时警示后发、留在最终态
      expect(useAppStore.getState().toast?.text).toContain("SCN-9");
    });
    // 切分完成后「未切分」提示消失
    expect(screen.queryByText("未切分")).not.toBeInTheDocument();
  });

  it("无跳过分镜时切分成功提示展示落格格数", async () => {
    vi.spyOn(API, "getGrid").mockResolvedValue(makeGrid({ split_at: null }));
    vi.spyOn(API, "splitGrid").mockResolvedValue({
      success: true,
      split_at: "2026-07-16T01:00:00Z",
      updated_scene_ids: ["SCN-1", "SCN-2"],
      missing_scene_ids: [],
      asset_fingerprints: {},
    });

    render(<GridPreviewPanel projectName="demo" gridIds={["grid-1"]} defaultExpanded />);
    fireEvent.click(await screen.findByText("切分落格"));

    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toContain("已切分 2 格");
    });
  });

  it("联合图就绪但未落格时展示「未切分」提示", async () => {
    vi.spyOn(API, "getGrid").mockResolvedValue(makeGrid({ split_at: null }));
    render(<GridPreviewPanel projectName="demo" gridIds={["grid-1"]} defaultExpanded />);
    expect(await screen.findByText("未切分")).toBeInTheDocument();
  });

  it("生成在途时切分按钮禁用", async () => {
    vi.spyOn(API, "getGrid").mockResolvedValue(makeGrid({ split_at: null }));
    useTasksStore.setState({ tasks: [makeTask()], optimisticActive: new Set() });

    render(<GridPreviewPanel projectName="demo" gridIds={["grid-1"]} defaultExpanded />);

    const splitBtn = (await screen.findByText("切分落格")).closest("button");
    expect(splitBtn?.disabled).toBe(true);
  });
});

describe("GridPreviewPanel upload", () => {
  it("选择文件后上传联合图：应用指纹、触发 grids 失效重拉并弹成功提示", async () => {
    vi.spyOn(API, "getGrid").mockResolvedValue(makeGrid({ split_at: null }));
    const { useProjectsStore } = await import("@/stores/projects-store");
    vi.spyOn(API, "uploadGridImage").mockResolvedValue({
      success: true,
      path: "grids/grid-1.png",
      version: 3,
      asset_fingerprints: { "grids/grid-1.png": 99 },
    });
    const revisionBefore = useAppStore.getState().gridsRevision;

    const { container } = render(
      <GridPreviewPanel projectName="demo" gridIds={["grid-1"]} defaultExpanded />,
    );

    await screen.findByText("上传联合图");
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).toBeTruthy();
    const file = new File([new Uint8Array([1, 2, 3])], "big.jpg", { type: "image/jpeg" });
    fireEvent.change(input!, { target: { files: [file] } });

    await waitFor(() => {
      expect(API.uploadGridImage).toHaveBeenCalledWith("demo", "grid-1", expect.any(File));
      expect(useProjectsStore.getState().assetFingerprints["grids/grid-1.png"]).toBe(99);
      expect(useAppStore.getState().gridsRevision).toBe(revisionBefore + 1);
      expect(useAppStore.getState().toast?.text).toContain("联合图已上传");
    });
  });
});

describe("GridPreviewPanel 版本时光机跨宫格切换", () => {
  it("切换宫格清空旧数据卸载版本时光机，上一张在途的版本列表响应不落进新宫格的面板", async () => {
    const version = (v: number) => ({
      version: v,
      filename: `v${v}.png`,
      created_at: "2026-07-16T00:00:00Z",
      file_size: 1,
      is_current: false,
    });
    let resolveStale: (r: { resource_type: string; resource_id: string; current_version: number; versions: ReturnType<typeof version>[] }) => void = () => {};

    vi.spyOn(API, "getGrid").mockImplementation(async (_p, id) => makeGrid({ id }));
    vi.spyOn(API, "getVersions").mockImplementation(async (_p, _t, resourceId) => {
      if (resourceId === "grid-1") {
        return new Promise((resolve) => {
          resolveStale = resolve;
        });
      }
      return { resource_type: "grids", resource_id: resourceId, current_version: 3, versions: [version(3)] };
    });

    render(<GridPreviewPanel projectName="demo" gridIds={["grid-1", "grid-2"]} defaultExpanded />);

    // grid-1 的版本列表请求发出后不解析，切到 grid-2 并读到它自己的版本；
    // 保护来自切换时的 setGrid(null) 卸载，改成加载期间留旧数据渲染即回归
    fireEvent.click(await screen.findByLabelText("版本"));
    await waitFor(() => expect(API.getVersions).toHaveBeenCalledWith("demo", "grids", "grid-1"));
    fireEvent.click(screen.getByText("2"));
    fireEvent.click(await screen.findByLabelText("版本"));
    await waitFor(() => expect(screen.getByText("v3")).toBeInTheDocument());

    resolveStale({ resource_type: "grids", resource_id: "grid-1", current_version: 7, versions: [version(7)] });

    await waitFor(() => expect(screen.getByText("v3")).toBeInTheDocument());
    expect(screen.queryByText("v7")).not.toBeInTheDocument();
  });
});
