import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";
import { useCostStore } from "@/stores/cost-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useWorkflowStore, workflowPlanKey } from "@/stores/workflow-store";
import { makePlan, makeStatus, makeTask } from "@/test/factories";
import { TimelineCanvas } from "./TimelineCanvas";
import type { NarrationEpisodeScript, ProjectData } from "@/types";

vi.mock("./ScriptReviewGate", async () => {
  const { scriptReviewGateMock } = await import("@/__mocks__/ScriptReviewGate");
  return scriptReviewGateMock();
});
vi.mock("./ShotSplitView", () => ({
  ShotSplitView: ({
    onUpdatePrompt,
    onGenerateNarration,
    onInsertShot,
    onRemoveShot,
  }: {
    onUpdatePrompt?: unknown;
    onGenerateNarration?: unknown;
    onInsertShot?: (afterId: string, novelText?: string) => Promise<boolean>;
    onRemoveShot?: (itemId: string) => Promise<boolean>;
  }) => (
    <div
      data-testid="shot-split-view"
      data-can-update-prompt={onUpdatePrompt ? "yes" : "no"}
      data-can-generate-narration={onGenerateNarration ? "yes" : "no"}
      data-can-insert-shot={onInsertShot ? "yes" : "no"}
      data-can-remove-shot={onRemoveShot ? "yes" : "no"}
    >
      <button type="button" onClick={() => void onInsertShot?.("SEG-1", "风停了。")}>insert</button>
      <button type="button" onClick={() => void onRemoveShot?.("SEG-1")}>remove</button>
    </div>
  ),
}));

/** 集页路由给画布的视图：缺省停在分镜视图。 */
const BOARD = { view: "board", onViewChange: () => {} } as const;

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

describe("TimelineCanvas", () => {
  beforeEach(() => {
    useCostStore.setState(useCostStore.getInitialState(), true);
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    useWorkflowStore.getState().resetTarget();
    vi.spyOn(API, "getCostEstimate").mockResolvedValue({
      project_name: "demo",
      models: { image: { provider: "p", model: "m" }, video: { provider: "p", model: "m" } },
      episodes: [],
      project_totals: { estimate: {}, actual: {} },
      unpriced: { estimate: [], actual: [] },
      missing_local_calls: false,
    });
  });

  it("shows the editable shot view once a script with segments is present", () => {
    render(
      <TimelineCanvas
        {...BOARD}
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
        {...BOARD}
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
        {...BOARD}
        projectName="demo"
        episode={1}
        hasDraft
        episodeScript={null}
        projectData={projectData}
      />,
    );

    expect(screen.getByText("脚本尚未生成，先在「脚本规划」中完成内容确认")).toBeInTheDocument();
    expect(screen.queryByTestId("shot-split-view")).not.toBeInTheDocument();
  });

  it("forwards shot insert and remove with the active episode script file", () => {
    const onInsertShot = vi.fn().mockResolvedValue(true);
    const onRemoveShot = vi.fn().mockResolvedValue(true);
    render(
      <TimelineCanvas
        {...BOARD}
        projectName="demo"
        episode={1}
        hasDraft
        episodeScript={makeScript()}
        scriptFile="scripts/episode_1.json"
        projectData={makeProjectData()}
        onInsertShot={onInsertShot}
        onRemoveShot={onRemoveShot}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "insert" }));
    fireEvent.click(screen.getByRole("button", { name: "remove" }));

    expect(onInsertShot).toHaveBeenCalledWith("SEG-1", "风停了。", "scripts/episode_1.json");
    expect(onRemoveShot).toHaveBeenCalledWith("SEG-1", "scripts/episode_1.json");
  });

  it("counts what each batch would fill in and greys out the ones with nothing missing", () => {
    useWorkflowStore.setState({
      plan: makePlan({
        status: makeStatus({
          artifacts: {
            storyboards: { current_ids: [], stale_ids: [], missing_ids: ["SEG-1", "SEG-2", "SEG-3"] },
            videos: { current_ids: ["SEG-1"], stale_ids: ["SEG-2"], missing_ids: [] },
            audio: { state: "not_applicable" },
          },
        }),
      }),
      planKey: workflowPlanKey("demo", 1),
    });
    // 正在生成的不算缺口
    useTasksStore.setState({
      tasks: [makeTask({ project_name: "demo", task_type: "storyboard", resource_id: "SEG-2", status: "running" })],
    });

    render(
      <TimelineCanvas
        {...BOARD}
        projectName="demo"
        episode={1}
        hasDraft
        episodeScript={makeScript()}
        projectData={makeProjectData()}
        onGenerateEpisodeNarration={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "补齐分镜图 · 2" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "视频已齐" })).toBeDisabled();
    // 本项目不涉及配音产物时数不出缺口：不写数量，确认与否交给入队
    expect(screen.getByRole("button", { name: "补齐旁白配音" })).toBeEnabled();
  });

  it("opens the batch preview for the storyboards that are still missing", async () => {
    const preview = vi
      .spyOn(API, "previewStoryboardBatch")
      .mockResolvedValue({ targets: [{ unit_id: "SEG-1" }], skipped: [], estimated_cost: null });
    render(
      <TimelineCanvas
        {...BOARD}
        projectName="demo"
        episode={1}
        hasDraft
        episodeScript={makeScript()}
        projectData={makeProjectData()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "补齐分镜图" }));

    expect(await screen.findByRole("alertdialog", { name: "补齐分镜图" })).toBeInTheDocument();
    expect(preview).toHaveBeenCalledWith("demo", 1, "storyboards", expect.anything());
  });

  it("shows the script plan instead of the shots and their batch actions on the plan view", () => {
    render(
      <TimelineCanvas
        {...BOARD}
        view="plan"
        projectName="demo"
        episode={1}
        hasDraft
        episodeScript={makeScript()}
        projectData={makeProjectData()}
      />,
    );

    expect(screen.getByTestId("script-review-gate")).toBeInTheDocument();
    expect(screen.queryByTestId("shot-split-view")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /补齐/ })).not.toBeInTheDocument();
  });

  it("shows the select-episode hint when there is no project data and no draft", () => {
    render(
      <TimelineCanvas
        {...BOARD}
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
        {...BOARD}
          projectName={DEMO_PROJECT_NAME}
          episode={1}
          hasDraft
          episodeScript={makeScript()}
          scriptFile="scripts/episode_1.json"
          projectData={makeProjectData()}
          onUpdatePrompt={vi.fn()}
          onMoveShot={vi.fn()}
          onInsertShot={vi.fn()}
          onRemoveShot={vi.fn()}
          onGenerateNarration={vi.fn()}
          onGenerateEpisodeNarration={vi.fn()}
        />,
      );
    }

    it("passes no write handlers down and drops the batch narration entry", () => {
      useProjectsStore.setState({ currentProjectName: DEMO_PROJECT_NAME });

      renderWithAllWriteHandlers();

      const shotView = screen.getByTestId("shot-split-view");
      expect(shotView).toHaveAttribute("data-can-update-prompt", "no");
      expect(shotView).toHaveAttribute("data-can-insert-shot", "no");
      expect(shotView).toHaveAttribute("data-can-remove-shot", "no");
      expect(shotView).toHaveAttribute("data-can-generate-narration", "no");
      expect(screen.queryByRole("button", { name: /补齐旁白配音/ })).not.toBeInTheDocument();
    });

    it("keeps the same write handlers outside the demo workbench", () => {
      useProjectsStore.setState({ currentProjectName: "demo" });

      renderWithAllWriteHandlers();

      const shotView = screen.getByTestId("shot-split-view");
      expect(shotView).toHaveAttribute("data-can-update-prompt", "yes");
      expect(shotView).toHaveAttribute("data-can-insert-shot", "yes");
      expect(shotView).toHaveAttribute("data-can-remove-shot", "yes");
      expect(shotView).toHaveAttribute("data-can-generate-narration", "yes");
      expect(screen.getByRole("button", { name: /补齐旁白配音/ })).toBeInTheDocument();
    });
  });
});
