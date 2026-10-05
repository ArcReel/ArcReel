import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData } from "@/types";

import { useMoveEpisode } from "./useMoveEpisode";

const PROJECT: ProjectData = {
  title: "Demo",
  content_mode: "narration",
  style: "",
  episodes: [
    { episode: 1, title: "开端", script_file: "scripts/episode_1.json", source_origin: "own" },
    { episode: 2, title: "番外", script_file: "scripts/episode_2.json", source_origin: "own" },
  ],
  characters: {},
  scenes: {},
  props: {},
};

describe("useMoveEpisode", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: PROJECT });
  });

  it("warns when the project data fails to refresh after the episode was moved", async () => {
    vi.spyOn(API, "getProject").mockRejectedValue(new Error("offline"));
    const move = vi.spyOn(API, "moveEpisode").mockResolvedValue(undefined);
    const { result } = renderHook(() => useMoveEpisode("demo"));

    await act(() => result.current(2, null));

    expect(move).toHaveBeenCalledWith("demo", 2, null);
    expect(useAppStore.getState().toast).toMatchObject({
      text: "操作已完成，但页面数据刷新失败，请手动刷新查看最新状态",
      tone: "warning",
    });
  });
});
