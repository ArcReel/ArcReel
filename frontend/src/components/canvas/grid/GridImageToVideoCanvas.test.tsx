import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useCostStore } from "@/stores/cost-store";
import { useTasksStore } from "@/stores/tasks-store";
import type {
  NarrationEpisodeScript,
  ProjectData,
  ReferenceGenerationRequestOptions,
} from "@/types";
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
  }: {
    onGenerateVideo?: (
      segmentId: string,
      requestOptions?: ReferenceGenerationRequestOptions,
    ) => void;
  }) => (
    <button
      type="button"
      onClick={() =>
        onGenerateVideo?.("SEG-1", {
          narration_delivery: "use_tts",
          confirmed_request_duration_seconds: 8,
        })
      }
    >
      generate-video-with-tts
    </button>
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
        transition_to_next: "cut",
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

  it("forwards narration delivery and confirmation through the grid canvas", () => {
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

    fireEvent.click(screen.getByRole("button", { name: "generate-video-with-tts" }));

    expect(onGenerateVideo).toHaveBeenCalledWith("SEG-1", "scripts/episode_1.json", {
      narration_delivery: "use_tts",
      confirmed_request_duration_seconds: 8,
    });
  });
});
