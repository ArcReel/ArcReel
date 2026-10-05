import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData } from "@/types";

import { CreateEpisodeDialog } from "./CreateEpisodeDialog";

const PROJECT: ProjectData = {
  title: "Demo",
  content_mode: "narration",
  style: "",
  episodes: [{ episode: 1, title: "开端", script_file: "scripts/episode_1.json", source_origin: "own" }],
  characters: {},
  scenes: {},
  props: {},
};

describe("CreateEpisodeDialog", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: PROJECT });
  });

  it("reports the new episode once the project data has refreshed", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({ project: PROJECT, scripts: {} });
    vi.spyOn(API, "createEpisode").mockResolvedValue({ episode: 2 });
    const onCreated = vi.fn();
    render(<CreateEpisodeDialog projectName="demo" onClose={() => {}} onCreated={onCreated} />);

    fireEvent.click(screen.getByRole("button", { name: "新建" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(2));
  });

  it("warns and does not point at the new episode when the project data fails to refresh", async () => {
    vi.spyOn(API, "getProject").mockRejectedValue(new Error("offline"));
    vi.spyOn(API, "createEpisode").mockResolvedValue({ episode: 2 });
    const onCreated = vi.fn();
    render(<CreateEpisodeDialog projectName="demo" onClose={() => {}} onCreated={onCreated} />);

    fireEvent.click(screen.getByRole("button", { name: "新建" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(null));
    expect(useAppStore.getState().toast).toMatchObject({
      text: "操作已完成，但页面数据刷新失败，请手动刷新查看最新状态",
      tone: "warning",
    });
  });
});
