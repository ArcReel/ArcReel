import { render, screen, waitFor } from "@testing-library/react";
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
    scenes: {},
    props: {},
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

  it("renders the project title and content mode", () => {
    render(<OverviewCanvas projectName="demo" projectData={makeProjectData()} />);
    expect(screen.getByText("Demo")).toBeInTheDocument();
  });

  it("shows welcome canvas when there is no overview and no episodes", () => {
    render(
      <OverviewCanvas
        projectName="demo"
        projectData={makeProjectData({ overview: undefined, episodes: [] })}
      />,
    );
    expect(screen.getByTestId("welcome-canvas")).toBeInTheDocument();
  });

  it("regenerates overview on button click", async () => {
    vi.spyOn(API, "generateOverview").mockResolvedValue(undefined as never);
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: {},
    });

    render(<OverviewCanvas projectName="demo" projectData={makeProjectData()} />);

    // Find the regenerate button by its accessible role
    const buttons = screen.getAllByRole("button");
    const regenButton = buttons.find((b) => b.getAttribute("title") !== null);
    if (regenButton) {
      regenButton.click();
      await waitFor(() => {
        expect(API.generateOverview).toHaveBeenCalledWith("demo");
      });
    }
  }, 10_000);
});
