import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";
import type { GridGeneration } from "@/types/grid";
import type { TaskItem } from "@/types";
import type { NarrationSegment } from "@/types";
import { GridPreviewView } from "./GridPreviewView";

// 面板本身有独立测试，这里只关心批次预览的档位与张数，避免拉进它的数据加载。
vi.mock("./GridPreviewPanel", () => ({
  GridPreviewPanel: () => <div data-testid="grid-panel" />,
}));

function makeSegments(count: number): NarrationSegment[] {
  return Array.from({ length: count }, (_, i) => ({
    segment_id: `SEG-${i + 1}`,
    episode: 1,
    duration_seconds: 5,
    segment_break: false,
    novel_text: "",
    characters_in_segment: [],
    image_prompt: "",
    video_prompt: "",
  })) as NarrationSegment[];
}

function renderView(segments: NarrationSegment[], onGenerateGrid?: () => Promise<void>) {
  return render(
    <GridPreviewView
      projectName="demo"
      episode={1}
      scriptFile="episode_1.json"
      segments={segments}
      contentMode="narration"
      onGenerateGrid={onGenerateGrid}
    />,
  );
}

function gridFor(sceneIds: string[]): GridGeneration {
  return {
    id: "grid-1",
    episode: 1,
    script_file: "episode_1.json",
    scene_ids: sceneIds,
    grid_image_path: "grids/grid-1.png",
    rows: 2,
    cols: 2,
    cell_count: 4,
    frame_chain: [],
    status: "completed",
    prompt: null,
    provider: "gemini",
    model: "gemini-image",
    grid_size: "grid_4",
    created_at: "2026-07-16T00:00:00Z",
    error_message: null,
    split_at: "2026-07-16T00:10:00Z",
  };
}

function runningGridTask(): TaskItem {
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
  };
}

beforeEach(() => {
  vi.spyOn(API, "listGrids").mockResolvedValue([]);
  useTasksStore.setState({ tasks: [], optimisticActive: new Set(), optimisticActiveScriptFile: new Set() });
});

describe("GridPreviewView 的档位与批次预览", () => {
  it("放行大宫格时按 5×5 切块，批次数取实际入队张数", async () => {
    vi.spyOn(API, "getGridCapability").mockResolvedValue({
      large_grid_allowed: true,
      max_cell_count: 25,
    });

    renderView(makeSegments(30));

    // 30 格按 25 一张切成 2 张，摘要的批次数须跟随张数而非分组数（分组只有 1 个）
    await waitFor(() => expect(screen.getByText(/^1 组 · 2 张联合图 · 30 格/)).toBeInTheDocument());
    expect(screen.getByText(/5×5/)).toBeInTheDocument();
  });

  it("门控生效时封顶 3×3，同一批场景切成更多张", async () => {
    vi.spyOn(API, "getGridCapability").mockResolvedValue({
      large_grid_allowed: false,
      max_cell_count: 9,
    });

    renderView(makeSegments(30));

    await waitFor(() => expect(screen.getByText(/^1 组 · 4 张联合图 · 30 格/)).toBeInTheDocument());
    expect(screen.getByText(/3×3/)).toBeInTheDocument();
  });

  it("能力请求失败时按保守上限展示，不虚报大宫格", async () => {
    vi.spyOn(API, "getGridCapability").mockRejectedValue(new Error("boom"));

    renderView(makeSegments(30));

    await waitFor(() => expect(screen.getByText(/3×3/)).toBeInTheDocument());
    expect(screen.getByText(/^1 组 · 4 张联合图 · 30 格/)).toBeInTheDocument();
  });
});

describe("GridPreviewView 的分组生成", () => {
  beforeEach(() => {
    vi.spyOn(API, "getGridCapability").mockResolvedValue({ large_grid_allowed: false, max_cell_count: 9 });
  });

  it("这一组还没有联合图时，「生成这一组」只提交这一组的分镜", async () => {
    const onGenerateGrid = vi.fn().mockResolvedValue(undefined);
    renderView(makeSegments(4), onGenerateGrid);

    fireEvent.click(await screen.findByRole("button", { name: "生成这一组" }));

    await waitFor(() =>
      expect(onGenerateGrid).toHaveBeenCalledWith(1, "episode_1.json", ["SEG-1", "SEG-2", "SEG-3", "SEG-4"]),
    );
  });

  it("这一组的联合图正在生成时，「重新生成这一组」不可用", async () => {
    vi.mocked(API.listGrids).mockResolvedValue([gridFor(["SEG-1", "SEG-2", "SEG-3", "SEG-4"])]);
    useTasksStore.setState({ tasks: [runningGridTask()] });
    renderView(makeSegments(4), vi.fn().mockResolvedValue(undefined));

    expect(await screen.findByRole("button", { name: "重新生成这一组" })).toBeDisabled();
  });
});
