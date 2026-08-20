import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";
import { useCostStore } from "@/stores/cost-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { TimelineCanvas } from "./TimelineCanvas";
import type { NarrationEpisodeScript, ProjectData } from "@/types";

vi.mock("./ScriptReviewGate", () => ({
  ScriptReviewGate: () => <div data-testid="script-review-gate" />,
}));
vi.mock("./ShotSplitView", () => ({
  ShotSplitView: ({
    onUpdatePrompt,
    onGenerateNarration,
  }: {
    onUpdatePrompt?: unknown;
    onGenerateNarration?: unknown;
  }) => (
    <div
      data-testid="shot-split-view"
      data-can-update-prompt={onUpdatePrompt ? "yes" : "no"}
      data-can-generate-narration={onGenerateNarration ? "yes" : "no"}
    />
  ),
}));
vi.mock("./EpisodeHeader", () => ({
  EpisodeHeader: ({ canEditTitle }: { canEditTitle?: boolean }) => (
    <div data-testid="episode-header" data-can-edit-title={canEditTitle ? "yes" : "no"} />
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

    expect(screen.getByText("脚本尚未生成，先在「内容整理」中完成内容确认")).toBeInTheDocument();
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

  describe("in the demo workbench", () => {
    afterEach(() => {
      useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    });

    // 写回调一律给足：只有演示态判定本身能把它们作废
    function renderWithAllWriteHandlers() {
      return render(
        <TimelineCanvas
          projectName={DEMO_PROJECT_NAME}
          episode={1}
          hasDraft
          episodeScript={makeScript()}
          scriptFile="scripts/episode_1.json"
          projectData={makeProjectData()}
          onUpdatePrompt={vi.fn()}
          onMoveShot={vi.fn()}
          onGenerateNarration={vi.fn()}
          onGenerateEpisodeNarration={vi.fn()}
          onSaveTitle={vi.fn()}
          canEditTitle
        />,
      );
    }

    it("passes no write handlers down and drops the batch narration entry", () => {
      useProjectsStore.setState({ currentProjectName: DEMO_PROJECT_NAME });

      renderWithAllWriteHandlers();

      const shotView = screen.getByTestId("shot-split-view");
      expect(shotView).toHaveAttribute("data-can-update-prompt", "no");
      expect(shotView).toHaveAttribute("data-can-generate-narration", "no");
      expect(screen.getByTestId("episode-header")).toHaveAttribute("data-can-edit-title", "no");
      expect(screen.queryByRole("button", { name: /生成全集旁白/ })).toBeNull();
    });

    it("keeps the same write handlers outside the demo workbench", () => {
      useProjectsStore.setState({ currentProjectName: "demo" });

      renderWithAllWriteHandlers();

      const shotView = screen.getByTestId("shot-split-view");
      expect(shotView).toHaveAttribute("data-can-update-prompt", "yes");
      expect(shotView).toHaveAttribute("data-can-generate-narration", "yes");
      expect(screen.getByTestId("episode-header")).toHaveAttribute("data-can-edit-title", "yes");
      expect(screen.getByRole("button", { name: /生成全集旁白/ })).toBeInTheDocument();
    });
  });
});
