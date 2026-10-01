import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useCostStore } from "@/stores/cost-store";
import { useTasksStore } from "@/stores/tasks-store";
import type { NarrationEpisodeScript, ProjectData } from "@/types";
import { GridImageToVideoCanvas } from "./GridImageToVideoCanvas";

vi.mock("../timeline/ScriptReviewGate", async () => {
  const { scriptReviewGateMock } = await import("@/__mocks__/ScriptReviewGate");
  return scriptReviewGateMock();
});
vi.mock("../timeline/EpisodeHeader", async () => {
  const { episodeHeaderMock } = await import("@/__mocks__/EpisodeHeader");
  return episodeHeaderMock();
});
vi.mock("./GridPreviewView", () => ({
  GridPreviewView: () => <div data-testid="grid-preview-view" />,
}));
vi.mock("../timeline/ShotSplitView", () => ({
  ShotSplitView: ({
    onGenerateVideo,
    onGenerateStoryboard,
    onMoveShot,
    onInsertShot,
    onRemoveShot,
    durationEndpointFixed,
  }: {
    onGenerateVideo?: (segmentId: string) => void;
    onGenerateStoryboard?: (segmentId: string) => void;
    onMoveShot?: (shotId: string, afterId: string | null) => Promise<boolean>;
    onInsertShot?: (afterId: string | null) => Promise<boolean>;
    onRemoveShot?: (itemId: string) => Promise<boolean>;
    durationEndpointFixed?: boolean;
  }) => (
    <>
      <button
        type="button"
        data-duration-endpoint-fixed={durationEndpointFixed ? "yes" : "no"}
        onClick={() => onGenerateVideo?.("SEG-1")}
      >
        generate-video
      </button>
      <button type="button" onClick={() => onGenerateStoryboard?.("SEG-2")}>generate-storyboard</button>
      <button type="button" onClick={() => void onInsertShot?.("SEG-1")}>insert</button>
      <button type="button" onClick={() => void onMoveShot?.("SEG-2", null)}>move</button>
      <button type="button" onClick={() => void onRemoveShot?.("SEG-1")}>remove</button>
    </>
  ),
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
      },
    ],
  };
}

describe("GridImageToVideoCanvas", () => {
  beforeEach(() => {
    useCostStore.setState(useCostStore.getInitialState(), true);
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getCostEstimate").mockResolvedValue({
      project_name: "demo",
      models: { image: { provider: "p", model: "m" }, video: { provider: "p", model: "m" } },
      episodes: [],
      project_totals: { estimate: {}, actual: {} },
    });
  });

  it("forwards the shot's video request with the canvas script file", () => {
    const onGenerateVideo = vi.fn();
    render(
      <GridImageToVideoCanvas
        projectName="demo"
        episode={1}
        hasDraft
        episodeScript={makeScript()}
        scriptFile="scripts/episode_1.json"
        projectData={makeProjectData()}
        onGenerateVideo={onGenerateVideo}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "generate-video" }));

    expect(onGenerateVideo).toHaveBeenCalledWith("SEG-1", "scripts/episode_1.json");
  });

  it("forwards endpoint-fixed duration to grid shot controls", () => {
    render(
      <GridImageToVideoCanvas
        projectName="demo" episode={1} hasDraft episodeScript={makeScript()}
        scriptFile="scripts/episode_1.json" projectData={makeProjectData()}
        durationEndpointFixed
      />,
    );
    expect(screen.getByRole("button", { name: "generate-video" })).toHaveAttribute(
      "data-duration-endpoint-fixed", "yes",
    );
  });

  it("wires timeline structure edits and single storyboard generation with the canvas script file", () => {
    const onMoveShot = vi.fn().mockResolvedValue(true);
    const onInsertShot = vi.fn().mockResolvedValue(true);
    const onRemoveShot = vi.fn().mockResolvedValue(true);
    const onGenerateStoryboard = vi.fn();
    render(
      <GridImageToVideoCanvas
        projectName="demo" episode={1} episodeScript={makeScript()}
        scriptFile="scripts/episode_1.json" projectData={makeProjectData()}
        onMoveShot={onMoveShot} onInsertShot={onInsertShot} onRemoveShot={onRemoveShot}
        onGenerateStoryboard={onGenerateStoryboard}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "insert" }));
    fireEvent.click(screen.getByRole("button", { name: "move" }));
    fireEvent.click(screen.getByRole("button", { name: "remove" }));
    fireEvent.click(screen.getByRole("button", { name: "generate-storyboard" }));

    expect(onInsertShot).toHaveBeenCalledWith("SEG-1", undefined, "scripts/episode_1.json");
    expect(onMoveShot).toHaveBeenCalledWith("SEG-2", null, "scripts/episode_1.json");
    expect(onRemoveShot).toHaveBeenCalledWith("SEG-1", "scripts/episode_1.json");
    expect(onGenerateStoryboard).toHaveBeenCalledWith("SEG-2", "scripts/episode_1.json");
  });
});
