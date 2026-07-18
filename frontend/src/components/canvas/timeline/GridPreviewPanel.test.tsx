import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useTasksStore, selectHasActiveTaskForScriptFile } from "@/stores/tasks-store";
import type { GridGeneration } from "@/types/grid";
import { GridPreviewPanel } from "./GridPreviewPanel";

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
