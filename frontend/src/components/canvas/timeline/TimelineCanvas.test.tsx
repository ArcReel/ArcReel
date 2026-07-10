import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useCostStore } from "@/stores/cost-store";
import { useTasksStore } from "@/stores/tasks-store";
import { TimelineCanvas } from "./TimelineCanvas";
import type { NarrationEpisodeScript, ProjectData } from "@/types";

vi.mock("./ScriptReviewGate", () => ({
  ScriptReviewGate: () => <div data-testid="script-review-gate" />,
}));
vi.mock("./ShotSplitView", () => ({
  ShotSplitView: () => <div data-testid="shot-split-view" />,
}));
vi.mock("./EpisodeHeader", () => ({
  EpisodeHeader: () => <div data-testid="episode-header" />,
}));
vi.mock("./AdReferenceUnitsPanel", () => ({
  AdReferenceUnitsPanel: () => <div data-testid="ad-reference-units-panel" />,
}));

function makeProjectData(): ProjectData {
  return {
    title: "Demo",
    content_mode: "narration",
    style: "Anime",
    episodes: [{ episode: 1, title: "EP1", script_file: "scripts/episode_1.json" }],
    characters: {},
  };
}

function makeScript(): NarrationEpisodeScript {
  return {
    episode: 1,
    title: "EP1",
    content_mode: "narration",
    duration_seconds: 4,
    novel: { title: "n", chapter: "1" },
    segments: [
      {
        segment_id: "SEG-1",
        episode: 1,
        duration_seconds: 4,
        segment_break: false,
        novel_text: "text",
        characters_in_segment: [],
        scenes: [],
        props: [],
        image_prompt: "p",
        video_prompt: "v",
        transition_to_next: "cut",
      },
    ],
  };
}

describe("TimelineCanvas", () => {
  beforeEach(() => {
    useCostStore.setState(useCostStore.getInitialState(), true);
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.spyOn(API, "getCostEstimate").mockResolvedValue({
      project_name: "demo",
      models: { image: { provider: "p", model: "m" }, video: { provider: "p", model: "m" } },
      episodes: [],
      project_totals: { estimate: {}, actual: {} },
    });
  });

  it("shows the editable shot view once a script with segments is present", () => {
    render(
      <TimelineCanvas
        projectName="demo"
        episode={1}
        hasDraft
        episodeScript={makeScript()}
        projectData={makeProjectData()}
      />,
    );

    expect(screen.getByTestId("shot-split-view")).toBeInTheDocument();
  });

  it("shows a script-not-ready hint instead of a blank screen when the script reverts while the timeline tab stays active", () => {
    const projectData = makeProjectData();
    const { rerender } = render(
      <TimelineCanvas
        projectName="demo"
        episode={1}
        hasDraft
        episodeScript={makeScript()}
        projectData={projectData}
      />,
    );

    expect(screen.getByTestId("shot-split-view")).toBeInTheDocument();

    rerender(
      <TimelineCanvas
        projectName="demo"
        episode={1}
        hasDraft
        episodeScript={null}
        projectData={projectData}
      />,
    );

    expect(screen.getByText("剧本尚未生成，先在「预处理」中完成审阅")).toBeInTheDocument();
    expect(screen.queryByTestId("shot-split-view")).not.toBeInTheDocument();
  });

  it("shows the select-episode hint when there is no project data and no draft", () => {
    render(
      <TimelineCanvas
        projectName="demo"
        episode={1}
        episodeScript={null}
        projectData={null}
      />,
    );

    expect(screen.getByText("请在左侧选择剧集")).toBeInTheDocument();
  });
});
