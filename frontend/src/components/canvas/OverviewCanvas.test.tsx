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

  it("uploads and deletes the style reference image from the workspace", async () => {
    vi.spyOn(API, "uploadStyleImage").mockResolvedValue({
      success: true,
      style_image: "style_reference.png",
      style_description: "updated",
      url: "u",
    });
    vi.spyOn(API, "deleteStyleImage").mockResolvedValue({ success: true });
    vi.spyOn(API, "getProject")
      .mockResolvedValueOnce({
        project: makeProjectData({ style_image: "style_reference.png" }),
        scripts: {},
      })
      .mockResolvedValueOnce({
        project: makeProjectData(),
        scripts: {},
      });

    const { container, rerender } = render(
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

    rerender(
      <OverviewCanvas
        projectName="demo"
        projectData={makeProjectData({ style_image: "style_reference.png" })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Delete reference image" }));

    await waitFor(() => {
      expect(API.deleteStyleImage).toHaveBeenCalledWith("demo");
      expect(API.getProject).toHaveBeenCalledTimes(2);
    });
  }, 10_000);

  it("shows a save action only when style description is edited", async () => {
    vi.spyOn(API, "updateStyleDescription").mockResolvedValue({ success: true });
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ style_description: "new description" }),
      scripts: {},
    });

    render(
      <OverviewCanvas projectName="demo" projectData={makeProjectData()} />,
    );

    expect(
      screen.queryByRole("button", { name: "Save style description" }),
    ).not.toBeInTheDocument();

    fireEvent.change(
      screen.getByPlaceholderText(
        "After uploading a style reference image, the system will automatically analyze and populate the style description; you can also edit it manually.",
      ),
      {
        target: { value: "new description" },
      },
    );

    fireEvent.click(screen.getByRole("button", { name: "Save style description" }));

    await waitFor(() => {
      expect(API.updateStyleDescription).toHaveBeenCalledWith(
        "demo",
        "new description",
      );
      expect(API.getProject).toHaveBeenCalled();
    });
  });
});
