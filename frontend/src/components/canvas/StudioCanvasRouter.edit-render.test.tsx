import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { episodeEditViewPath } from "@/app-routes";
import { StudioCanvasRouter } from "@/components/canvas/StudioCanvasRouter";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import { makePlan, makeStatus } from "@/test/factories";
import type { EpisodeScript, ProjectData } from "@/types";
import type { EditTimelineReadout, EditTimelineSummary } from "@/types/edit-timeline";
import { stubMediaElementPlayback } from "@/test/media-element";

// 剪辑视图本体不 mock：这里验证路由层把空状态与出片按钮接到它的两个挂载点上。
vi.mock("@/components/workflow/WorkflowPanel", () => ({
  WorkflowPanel: () => <div data-testid="workflow-panel" />,
}));

const SUMMARY: EditTimelineSummary = {
  id: "tl-00000001",
  name: "初剪",
  episode: 1,
  revision: 1,
  clip_count: 1,
  created_at: "2026-09-30T08:00:00Z",
  updated_at: "2026-09-30T08:00:00Z",
  updated_by: { kind: "creator", user_id: null },
  update_summary: "按脚本新建",
  agent_turn: null,
};

function readout(issues: EditTimelineReadout["issues"]): EditTimelineReadout {
  return {
    timeline: { id: SUMMARY.id, name: SUMMARY.name, episode: 1 },
    revision: 1,
    latest_revision: 1,
    duration: 4,
    clips: [
      {
        id: "c1",
        unit_id: "SEG-1",
        status: issues.length > 0 ? "video_missing" : "ready",
        start: 0,
        duration: 4,
        video_version: issues.length > 0 ? null : 1,
        source_duration: issues.length > 0 ? null : 4,
        trim: null,
        source_volume: 1,
        hold: 0,
        carries_narration: false,
        narration: null,
        reason: null,
        transition_to_next: null,
      },
    ],
    bgm: [],
    issues,
  };
}

function makeScript(videoClip: string | null): EpisodeScript {
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
        image_prompt: "image prompt",
        video_prompt: "video prompt",
        generated_assets: {
          storyboard_image: null,
          storyboard_last_image: null,
          grid_id: null,
          grid_cell_index: null,
          video_clip: videoClip,
          video_thumbnail: null,
          video_uri: null,
          status: videoClip ? "completed" : "pending",
        },
      },
    ],
  };
}

function renderEditView(script: EpisodeScript) {
  const projectData: ProjectData = {
    title: "Demo",
    content_mode: "narration",
    style: "Anime",
    episodes: [{ episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "generated" }],
    characters: {},
    scenes: {},
    props: {},
  };
  useProjectsStore.setState({
    currentProjectName: "demo",
    currentProjectData: projectData,
    currentScripts: { "episode_1.json": script },
  });
  const { hook, searchHook } = memoryLocation({ path: episodeEditViewPath(1) });
  return render(
    <Router hook={hook} searchHook={searchHook}>
      <StudioCanvasRouter />
    </Router>,
  );
}

describe("StudioCanvasRouter edit view mounts", () => {
  beforeEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useAppStore.setState(useAppStore.getInitialState(), true);
    useWorkflowStore.setState({ plan: null, planKey: null });
    vi.restoreAllMocks();
    stubMediaElementPlayback();
  });

  it("creates a timeline from the empty state without opening the assistant, then offers render", async () => {
    vi.spyOn(API, "listEditTimelines")
      .mockResolvedValueOnce({ timelines: [] })
      .mockResolvedValue({ timelines: [SUMMARY] });
    const create = vi.spyOn(API, "createEditTimeline").mockResolvedValue(readout([]));
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(readout([]));
    useAppStore.setState({ assistantPanelOpen: false });

    renderEditView(makeScript("videos/scene_SEG-1.mp4"));

    fireEvent.click(await screen.findByRole("button", { name: "新建剪辑时间线" }));

    await waitFor(() => expect(create).toHaveBeenCalledWith("demo", 1, "完整版"));
    expect(await screen.findByRole("tab", { name: "初剪" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("button", { name: "出片" })).toBeEnabled();
    expect(useAppStore.getState().assistantPanelOpen).toBe(false);
  });

  it("disables the empty-state actions when the workflow refuses to create an edit timeline", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({ timelines: [] });
    const status = makeStatus({ operations: { create_edit_timeline: { state: "refused", reason: "no_available_video" } } });
    useWorkflowStore.setState({ plan: makePlan({ status }), planKey: "demo::1" });

    renderEditView(makeScript(null));

    expect(await screen.findByRole("button", { name: "新建剪辑时间线" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "交给 Agent 剪辑" })).toBeDisabled();
  });

  it("blocks render on a blocking issue and links to the issue list", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({ timelines: [SUMMARY] });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(
      readout([
        {
          code: "video_missing",
          severity: "blocking",
          applies_to: "all",
          clip_ids: ["c1"],
          unit_id: "SEG-1",
          params: {},
        },
      ]),
    );
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;

    renderEditView(makeScript("videos/scene_SEG-1.mp4"));

    const renderButton = await screen.findByRole("button", { name: "出片" });
    expect(renderButton).toBeDisabled();
    expect(renderButton.parentElement).toHaveAttribute("title", expect.stringContaining("SEG-1"));

    fireEvent.click(screen.getByRole("button", { name: "查看问题（1）" }));
    expect(scrollIntoView).toHaveBeenCalled();
  });
});
