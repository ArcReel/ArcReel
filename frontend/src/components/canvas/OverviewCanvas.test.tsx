import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { OverviewCanvas } from "./OverviewCanvas";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData } from "@/types";

vi.mock("./WelcomeCanvas", () => ({
  WelcomeCanvas: () => <div data-testid="welcome-canvas">welcome</div>,
}));

function makeProjectData(overrides: Partial<ProjectData> = {}): ProjectData {
  return {
    title: "Demo",
    content_mode: "narration",
    style: "Anime",
    style_description: "old description",
    overview: {
      synopsis: "summary",
      genre: "fantasy",
      theme: "growth",
      world_setting: "palace",
    },
    episodes: [{ episode: 1, title: "EP1", script_file: "scripts/episode_1.json" }],
    characters: {},
    clues: {},
    ...overrides,
  };
}

describe("OverviewCanvas", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.stubGlobal("confirm", vi.fn(() => true));
  });

  it("uploads the style reference image from the workspace", async () => {
    vi.spyOn(API, "uploadStyleImage").mockResolvedValue({
      success: true,
      style_image: "style_reference.png",
      style_description: "updated",
      url: "u",
    });
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ style_image: "style_reference.png" }),
      scripts: {},
    });

    const { container } = render(
      <OverviewCanvas projectName="demo" projectData={makeProjectData()} />,
    );

    const file = new File(["style"], "style.png", { type: "image/png" });
    const fileInput = container.querySelector("input[type='file']");
    expect(fileInput).not.toBeNull();

    fireEvent.change(fileInput as HTMLInputElement, { target: { files: [file] } });

    await waitFor(() => {
      expect(API.uploadStyleImage).toHaveBeenCalledWith("demo", file);
      expect(API.getProject).toHaveBeenCalledTimes(1);
    });
  }, 10_000);

  it("displays the style description from project data", () => {
    render(
      <OverviewCanvas
        projectName="demo"
        projectData={makeProjectData({ style_description: "cinematic noir" })}
      />,
    );

    expect(screen.getByText("cinematic noir")).toBeInTheDocument();
  });
});
