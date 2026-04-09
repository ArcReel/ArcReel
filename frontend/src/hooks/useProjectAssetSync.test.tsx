import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useProjectAssetSync } from "./useProjectAssetSync";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import type { TaskItem } from "@/types";

function makeTask(overrides: Partial<TaskItem> = {}): TaskItem {
  return {
    task_id: "task-1",
    project_name: "demo",
    task_type: "storyboard",
    media_type: "image",
    resource_id: "SEG-1",
    script_file: "episode_1.json",
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

describe("useProjectAssetSync", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("does not refresh on the initial snapshot of already-succeeded tasks", () => {
    useTasksStore.setState({
      tasks: [makeTask({ status: "succeeded" })],
    });
    const getProjectSpy = vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        content_mode: "narration",
        style: "Anime",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    });

    renderHook(() => useProjectAssetSync("demo"));

    expect(getProjectSpy).not.toHaveBeenCalled();
    expect(useAppStore.getState().getEntityRevision("segment:SEG-1")).toBe(0);
  });

  it("refreshes the current project exactly once when a tracked task becomes succeeded", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        content_mode: "narration",
        style: "Anime",
        episodes: [],
        characters: {},
        clues: {},
      },
      scripts: {},
    });

    renderHook(() => useProjectAssetSync("demo"));

    act(() => {
      useTasksStore.getState().setTasks([makeTask({ status: "running" })]);
    });

    act(() => {
      useTasksStore.getState().setTasks([
        makeTask({
          status: "succeeded",
          updated_at: "2026-02-01T00:01:00Z",
          finished_at: "2026-02-01T00:01:00Z",
        }),
      ]);
    });

    await waitFor(() => {
      expect(API.getProject).toHaveBeenCalledTimes(1);
      expect(useAppStore.getState().getEntityRevision("segment:SEG-1")).toBe(1);
      expect(useProjectsStore.getState().currentProjectName).toBe("demo");
    });

    act(() => {
      useTasksStore.getState().setTasks([
        makeTask({
          status: "succeeded",
          updated_at: "2026-02-01T00:01:00Z",
          finished_at: "2026-02-01T00:01:00Z",
        }),
      ]);
    });

    await waitFor(() => {
      expect(API.getProject).toHaveBeenCalledTimes(1);
    });
  });
});
