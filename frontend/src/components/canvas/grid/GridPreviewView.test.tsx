import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { NarrationSegment } from "@/types";
import { GridPreviewView } from "./GridPreviewView";

// 面板本身有独立测试，这里只关心批次预览的档位与张数，避免拉进它的数据加载。
vi.mock("@/components/canvas/timeline/GridPreviewPanel", () => ({
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
    transition_to_next: "cut",
  })) as NarrationSegment[];
}

function renderView(segments: NarrationSegment[]) {
  return render(
    <GridPreviewView
      projectName="demo"
      episode={1}
      scriptFile="episode_1.json"
      segments={segments}
      contentMode="narration"
    />,
  );
}

beforeEach(() => {
  vi.spyOn(API, "listGrids").mockResolvedValue([]);
});

describe("GridPreviewView 的档位与批次预览", () => {
  it("放行大宫格时按 5×5 切块，批次数取实际入队张数", async () => {
    vi.spyOn(API, "getGridCapability").mockResolvedValue({
      large_grid_allowed: true,
      max_cell_count: 25,
    });

    renderView(makeSegments(30));

    // 30 格按 25 一张切成 2 张，摘要的批次数须跟随张数而非分组数（分组只有 1 个）
    await waitFor(() => expect(screen.getByText(/^2 批 · 30 格/)).toBeInTheDocument());
    expect(screen.getByText(/5×5/)).toBeInTheDocument();
  });

  it("门控生效时封顶 3×3，同一批场景切成更多张", async () => {
    vi.spyOn(API, "getGridCapability").mockResolvedValue({
      large_grid_allowed: false,
      max_cell_count: 9,
    });

    renderView(makeSegments(30));

    await waitFor(() => expect(screen.getByText(/^4 批 · 30 格/)).toBeInTheDocument());
    expect(screen.getByText(/3×3/)).toBeInTheDocument();
  });

  it("能力请求失败时按保守上限展示，不虚报大宫格", async () => {
    vi.spyOn(API, "getGridCapability").mockRejectedValue(new Error("boom"));

    renderView(makeSegments(30));

    await waitFor(() => expect(screen.getByText(/3×3/)).toBeInTheDocument());
    expect(screen.getByText(/^4 批 · 30 格/)).toBeInTheDocument();
  });
});
