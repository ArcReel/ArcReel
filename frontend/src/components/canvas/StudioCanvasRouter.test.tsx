import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useProjectsStore } from "@/stores/projects-store";
import { selectActiveResourceIds, selectHasActiveTaskForScriptFile, useTasksStore } from "@/stores/tasks-store";
import { StudioCanvasRouter } from "@/components/canvas/StudioCanvasRouter";
import { episodeEditViewPath } from "@/app-routes";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";
import type { AdEpisodeScript, EpisodeScript, ProjectData } from "@/types";

// WorkflowPanel.test.tsx covers panel behavior; verify routing-supplied callbacks only here.
vi.mock("@/components/workflow/WorkflowPanel", () => ({
  WorkflowPanel: ({
    onRegenerate,
  }: {
    onRegenerate?: (stepId: string, unitIds: string[]) => void;
  }) => (
    <div data-testid="workflow-panel" data-can-regenerate={onRegenerate ? "yes" : "no"}>
      <button onClick={() => onRegenerate?.("storyboard", ["SEG-1"])}>workflow-regenerate</button>
      <button onClick={() => onRegenerate?.("video", ["SEG-1"])}>workflow-regenerate-video</button>
    </div>
  ),
}));

// EditTimelineView.test.tsx covers its own timeline reads/behavior; verify episode-page mounting here.
vi.mock("./edit/EditTimelineView", () => ({
  EditTimelineView: ({ episode }: { episode: number }) => (
    <div data-testid="edit-timeline-view">episode {episode}</div>
  ),
}));

vi.mock("./overview/OverviewCanvas", () => ({
  OverviewCanvas: () => <div data-testid="overview-canvas">Overview</div>,
}));

vi.mock("./episodes/EpisodesView", () => ({
  EpisodesView: ({ projectName }: { projectName: string }) => (
    <div data-testid="episodes-view">{projectName}</div>
  ),
}));

vi.mock("./timeline/TimelineCanvas", () => ({
  TimelineCanvas: ({
    episodeScript,
    scriptFile,
    durationOptions,
    onUpdatePrompt,
    onMoveShot,
    onInsertShot,
    onRemoveShot,
    onGenerateStoryboard,
    onGenerateVideo,
    onGenerateNarration,
    onGenerateEpisodeNarration,
    view,
  }: {
    view: string;
    episodeScript: unknown;
    scriptFile?: string;
    durationOptions?: number[];
    onUpdatePrompt?: (segmentId: string, patch: Record<string, unknown>, scriptFile?: string) => Promise<boolean>;
    onMoveShot?: (shotId: string, afterId: string | null, scriptFile?: string) => Promise<boolean> | void;
    onInsertShot?: (afterId: string, novelText: string | undefined, scriptFile?: string) => Promise<boolean>;
    onRemoveShot?: (itemId: string, scriptFile?: string) => Promise<boolean>;
    onGenerateStoryboard?: (segmentId: string) => void;
    onGenerateVideo?: (segmentId: string) => void;
    onGenerateNarration?: (segmentId: string) => void;
    onGenerateEpisodeNarration?: (scriptFile?: string) => void;
  }) => (
    <div data-testid="timeline-canvas" data-view={view}>
      <div data-testid="timeline-has-script">{episodeScript ? "yes" : "no"}</div>
      <div data-testid="timeline-duration-options">{(durationOptions ?? []).join(",")}</div>
      <button
        onClick={(e) => {
          const el = e.currentTarget;
          onUpdatePrompt?.("SEG-1", { image_prompt: "new prompt" }, scriptFile).then(
            (refreshed) => el.setAttribute("data-result", String(refreshed)),
            (err: Error) => el.setAttribute("data-error", err.message),
          );
        }}
      >
        update-prompt
      </button>
      <button
        onClick={(e) => {
          const el = e.currentTarget;
          el.setAttribute("data-update-pending", "true");
          void Promise.resolve(
            onUpdatePrompt?.("SEG-1", { image_prompt: "new prompt" }, scriptFile),
          ).then(() => {
            el.setAttribute("data-update-pending", "false");
          });
        }}
      >
        update-prompt-await
      </button>
      <button
        onClick={(e) => {
          const el = e.currentTarget;
          void Promise.resolve(onMoveShot?.("SEG-1", "SEG-2", scriptFile)).then((moved) => {
            el.setAttribute("data-move-result", String(moved));
          });
        }}
      >
        move-shot-later
      </button>
      <button
        onClick={(e) => {
          const el = e.currentTarget;
          void onInsertShot?.("SEG-1", undefined, scriptFile).then((ok) => el.setAttribute("data-result", String(ok)));
        }}
      >
        insert-shot
      </button>
      <button
        onClick={(e) => {
          const el = e.currentTarget;
          void onRemoveShot?.("SEG-1", scriptFile).then((ok) => el.setAttribute("data-result", String(ok)));
        }}
      >
        remove-shot
      </button>
      <button onClick={() => onGenerateStoryboard?.("SEG-1")}>generate-storyboard</button>
      <button onClick={() => onGenerateVideo?.("SEG-1")}>generate-video</button>
      {onGenerateNarration && <button onClick={() => onGenerateNarration("SEG-1")}>generate-narration</button>}
      {onGenerateEpisodeNarration && (
        <button onClick={() => onGenerateEpisodeNarration()}>generate-episode-narration</button>
      )}
    </div>
  ),
}));

vi.mock("./EpisodeSourceReview", () => ({
  EpisodeSourceReview: ({ projectName, episode }: { projectName: string; episode: number }) => (
    <div data-testid="episode-source-review">
      {projectName}::{episode}
    </div>
  ),
}));

vi.mock("./reference/ReferenceVideoCanvas", () => ({
  ReferenceVideoCanvas: ({
    hasScript,
    showPreprocess,
    freeDuration,
    view,
  }: {
    hasScript: boolean;
    showPreprocess?: boolean;
    freeDuration?: boolean;
    view: string;
  }) => (
    <div
      data-testid="reference-video-canvas"
      data-view={view}
      data-has-script={hasScript ? "yes" : "no"}
      data-preprocess={showPreprocess === false ? "no" : "yes"}
      data-free-duration={freeDuration ? "yes" : "no"}
    />
  ),
}));

vi.mock("./grid/GridImageToVideoCanvas", () => ({
  GridImageToVideoCanvas: ({
    onGenerateGrid,
    onGenerateVideo,
    view,
  }: {
    view: string;
    onGenerateGrid?: (
      episode: number,
      scriptFile: string,
      sceneIds?: string[],
    ) => void | Promise<void>;
    onGenerateVideo?: (segmentId: string, scriptFile?: string) => void | Promise<void>;
  }) => (
    <div data-testid="grid-canvas" data-view={view}>
      <button onClick={() => void onGenerateGrid?.(1, "episode_1.json")}>generate-grid</button>
      <button
        onClick={(event) => {
          const button = event.currentTarget;
          button.dataset.videoResult = "pending";
          void Promise.resolve(
            onGenerateVideo?.("SEG-1", "episode_1.json"),
          ).then(
            () => {
              button.dataset.videoResult = "resolved";
            },
            (error: unknown) => {
              button.dataset.videoResult = error instanceof Error ? error.name : "unknown";
            },
          );
        }}
      >
        generate-grid-video-await
      </button>
    </div>
  ),
}));

// Gallery doubles render one generation button per asset, triggering routing sheet-generation callbacks;
// restore/reload buttons write callback settlement values to data-result.
vi.mock("./lorebook/AssetGallery", () => ({
  AssetGallery: ({
    assetType,
    assets,
    onGenerate,
    onRestoreVersion,
    onReload,
  }: {
    assetType: string;
    assets: Record<string, unknown>;
    onGenerate: (name: string) => void;
    onRestoreVersion?: () => Promise<unknown> | void;
    onReload?: () => Promise<unknown> | void;
  }) => (
    <div data-testid={`${assetType}-gallery`} data-names={Object.keys(assets).join(",")}>
      {Object.keys(assets).map((name) => (
        <button key={name} onClick={() => onGenerate(name)}>
          generate-{assetType}
        </button>
      ))}
      <button
        onClick={(e) => {
          const el = e.currentTarget;
          void Promise.resolve(onRestoreVersion?.()).then((result) => el.setAttribute("data-result", String(result)));
        }}
      >
        restore-{assetType}
      </button>
      <button
        onClick={(e) => {
          const el = e.currentTarget;
          void Promise.resolve(onReload?.()).then((result) => el.setAttribute("data-result", String(result)));
        }}
      >
        reload-{assetType}
      </button>
    </div>
  ),
}));

const REFRESH_FAILED = "操作已完成，但页面数据刷新失败，请手动刷新查看最新状态";

/** Server video-capabilities response: narrowed durations already computed by project resolution/generation mode. */
function fakeVideoCapabilities(allowed: number[], raw: number[] = allowed) {
  return {
    provider_id: "gemini-aistudio",
    model: "veo-3.1",
    supported_durations: raw,
    max_duration: Math.max(...raw),
    max_reference_images: 3,
    first_frame: true,
    last_frame: true,
    source: "registry",
    voice_consistency: "soft",
    duration_constraints: {
      resolution: null,
      uses_reference_images: false,
      allowed,
      excluded: {},
    },
  } as Awaited<ReturnType<typeof API.getVideoCapabilities>>;
}

function makeProjectData(overrides: Partial<ProjectData> = {}): ProjectData {
  return {
    title: "Demo",
    content_mode: "narration",
    style: "Anime",
    episodes: [{ episode: 1, title: "EP1", script_file: "scripts/episode_1.json" }],
    characters: {
      Hero: { description: "hero description" },
    },
    scenes: { Temple: { description: "ancient temple" } },
    props: { Sword: { description: "rusty sword" } },
    ...overrides,
  };
}

function makeScript(): EpisodeScript {
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
        characters_in_segment: ["Hero"],
        scenes: ["Temple"],
        props: ["Sword"],
        image_prompt: "image prompt",
        video_prompt: "video prompt",
      },
    ],
  };
}

function makeAdScript(): EpisodeScript {
  return {
    episode: 1,
    title: "广告视频",
    content_mode: "ad",
    novel: { title: "n", chapter: "1" },
    shots: [
      {
        shot_id: "SEG-1",
        section: "hook",
        duration_seconds: 5,
        voiceover_text: "口播文案",
        image_prompt: "ad image prompt",
        video_prompt: "ad video prompt",
      },
    ],
  };
}

function makeDramaScript(): EpisodeScript {
  return {
    episode: 1,
    title: "EP1",
    content_mode: "drama",
    novel: { title: "n", chapter: "1" },
    scenes: [
      {
        scene_id: "SEG-1",
        duration_seconds: 6,
        segment_break: false,
        characters_in_scene: ["Hero"],
        image_prompt: "drama image prompt",
        video_prompt: "drama video prompt",
      },
    ],
  };
}

/** Edit header episode titles: pencil, type, Enter save. */
async function renameEpisode(title: string) {
  fireEvent.click(await screen.findByRole("button", { name: "编辑分集标题" }));
  const input = screen.getByRole("textbox", { name: "编辑分集标题" });
  fireEvent.change(input, { target: { value: title } });
  fireEvent.keyDown(input, { key: "Enter" });
}

function renderAt(path: string) {
  const { hook } = memoryLocation({ path });
  return render(
    <Router hook={hook}>
      <StudioCanvasRouter />
    </Router>,
  );
}

// Reproduce real nested routes: parent `/:projectName` provides `projectName`
// so StudioCanvasRouter detects demos before store currentProjectName catches up.
// Return `navigate` for in-place route switches without unmounting the same StudioCanvasRouter instance,
// matching real→demo timing because wouter parameter changes do not remount.
function renderAtProjectRoute(projectName: string, subPath: string) {
  const { hook, navigate } = memoryLocation({ path: `/${projectName}${subPath}` });
  const view = render(
    <Router hook={hook}>
      <Route path="/:projectName" nest>
        <StudioCanvasRouter />
      </Route>
    </Router>,
  );
  return { ...view, navigate };
}

describe("StudioCanvasRouter", () => {
  beforeEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useAppStore.setState(useAppStore.getInitialState(), true);
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("shows loading state when currentProjectName is missing", () => {
    renderAt("/");
    expect(screen.getByText("加载中…")).toBeInTheDocument();
  });

  it("keeps showing loading state on a deep link while the project detail request is still in flight", () => {
    // Initial load sets currentProjectName with empty data before refreshProject settles (see
    // router.tsx); nonempty names do not prove loaded details. Direct /characters
    // deep links would otherwise render interactive empty-project pages from empty collections.
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: null,
      projectDetailLoading: true,
    });

    renderAt("/characters");

    expect(screen.getByText("加载中…")).toBeInTheDocument();
    expect(screen.queryByTestId("character-gallery")).not.toBeInTheDocument();
  });

  it("routes characters/scenes/props/episodes views correctly", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: {
        "episode_1.json": makeScript(),
      },
    });

    const viewCharacters = renderAt("/characters");
    expect(screen.getByTestId("character-gallery")).toHaveAttribute("data-names", "Hero");
    viewCharacters.unmount();

    const viewScenes = renderAt("/scenes");
    expect(screen.getByTestId("scene-gallery")).toHaveAttribute("data-names", "Temple");
    viewScenes.unmount();

    const viewProps = renderAt("/props");
    expect(screen.getByTestId("prop-gallery")).toHaveAttribute("data-names", "Sword");
    viewProps.unmount();

    const viewEpisodeList = renderAt("/episodes");
    expect(screen.getByTestId("episodes-view")).toHaveTextContent("demo");
    viewEpisodeList.unmount();

    const viewEpisodes = renderAt("/episodes/1");
    expect(screen.getByTestId("timeline-canvas")).toBeInTheDocument();
    expect(screen.getByTestId("timeline-has-script")).toHaveTextContent("yes");
    viewEpisodes.unmount();

    await waitFor(() => {
      expect(screen.queryByText("加载中…")).not.toBeInTheDocument();
    });
  });

  it("sends ad projects away from the episodes view to the overview", () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ content_mode: "ad" }),
    });

    renderAt("/episodes");

    expect(screen.queryByTestId("episodes-view")).not.toBeInTheDocument();
    expect(screen.getByTestId("overview-canvas")).toBeInTheDocument();
  });

  it("shows an empty state for an unknown workspace path and leads back to the overview", async () => {
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: makeProjectData() });

    renderAt("/lorebook");

    expect(screen.getByRole("heading", { name: "这个页面不存在" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "回到概览" }));
    expect(await screen.findByTestId("overview-canvas")).toBeInTheDocument();
  });

  // Same-instance real→demo navigation changes nested projectName without remount;
  // clear old real-project duration capabilities or backend restrictions would apply
  // to fictional demo durations, falsely reporting incompatibility again.
  it("clears cached provider/backend duration capabilities when switching from a real project into the demo route", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(fakeVideoCapabilities([5, 10]));
    useProjectsStore.setState({
      currentProjectName: "real-project",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    const view = renderAtProjectRoute("real-project", "/episodes/1");
    await waitFor(() => {
      expect(screen.getByTestId("timeline-duration-options")).toHaveTextContent("5,10");
    });

    // Switch to demo in place without unmounting, reproducing same-instance timing
    // and StudioWorkspace effect synchronization into stores.
    useProjectsStore.setState({
      currentProjectName: DEMO_PROJECT_NAME,
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });
    view.navigate(`/${DEMO_PROJECT_NAME}/episodes/1`);

    await waitFor(() => {
      // Cache cleared: old backend capabilities no longer judge duration compatibility.
      expect(screen.getByTestId("timeline-duration-options")).toHaveTextContent("");
    });
  });

  // Per-shot duration candidates use server narrowing by project resolution/effective generation_mode; frontend owns neither
  // rules nor resolution/episode arguments, since servers evaluate saved execution-model tiers per project.
  it.each([
    ["1080p", [8]],
    ["720p", [4, 6, 8]],
  ])("takes the per-shot duration options from the server-side narrowing at %s", async (resolution, allowed) => {
    const VEO = "gemini-aistudio/veo-3.1";
    const capabilitiesSpy = vi
      .spyOn(API, "getVideoCapabilities")
      .mockResolvedValue(fakeVideoCapabilities(allowed, [4, 6, 8]));
    useProjectsStore.setState({
      currentProjectName: "real-project",
      currentProjectData: makeProjectData({
        video_backend: VEO,
        model_settings: { [VEO]: { resolution } },
      }),
      currentScripts: { "episode_1.json": makeScript() },
    });

    renderAtProjectRoute("real-project", "/episodes/1");
    await waitFor(() => {
      // Exact equality, not containment: full "4,6,8" also contains substring "8".
      expect(screen.getByTestId("timeline-duration-options")).toHaveTextContent(
        new RegExp(`^${allowed.join(",")}$`),
      );
    });
    expect(capabilitiesSpy).toHaveBeenCalledWith("real-project", expect.anything());
    const query = capabilitiesSpy.mock.calls[0]?.[1];
    // Servers resolve actual i2v/r2v buckets by generation_mode, without default-layer overrides.
    expect(query?.videoBackend).toBeUndefined();
    expect(query?.resolution).toBeUndefined();
    expect(query?.usesReferenceImages).toBeUndefined();
  });

  // Demo→real routes clear demoMode before currentProjectName leaves demo names;
  // demoMode alone would send doomed /video-capabilities requests for nonexistent server demo projects.
  it("skips the video-capabilities lookup for the stale demo project name after navigating to a real project, before the store catches up", async () => {
    const capabilitiesSpy = vi
      .spyOn(API, "getVideoCapabilities")
      .mockResolvedValue({ supported_durations: [5, 10] } as Awaited<
        ReturnType<typeof API.getVideoCapabilities>
      >);
    useProjectsStore.setState({
      currentProjectName: DEMO_PROJECT_NAME,
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    const view = renderAtProjectRoute(DEMO_PROJECT_NAME, "/episodes/1");
    await waitFor(() => {
      expect(screen.getByTestId("timeline-canvas")).toBeInTheDocument();
    });

    // Switch routes only, deliberately retaining demo currentProjectName,
    // reproducing the window before StudioWorkspace effects finish.
    view.navigate("/real-project/episodes/1");

    expect(capabilitiesSpy).not.toHaveBeenCalledWith(DEMO_PROJECT_NAME, expect.anything());
  });

  // Three script_status values derive from project-summary script_plan/formal-artifact states;
  // routing chooses source review/canvas accordingly, one case per state.
  it("sends an episode with no script_plan and no script to the source review", () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({
        episodes: [{ episode: 1, title: "EP1", script_file: "", script_status: "none" }],
      }),
      currentScripts: {},
    });

    renderAt("/episodes/1");

    expect(screen.getByTestId("episode-source-review")).toHaveTextContent("demo::1");
    expect(screen.queryByTestId("timeline-canvas")).not.toBeInTheDocument();
  });

  it("falls back to TimelineCanvas when a draft exists but the script hasn't been generated", () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({
        episodes: [
          { episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "segmented" },
        ],
      }),
      currentScripts: {},
    });

    renderAt("/episodes/1");

    expect(screen.getByTestId("timeline-canvas")).toBeInTheDocument();
    expect(screen.getByTestId("timeline-has-script")).toHaveTextContent("no");
    expect(screen.queryByTestId("episode-source-review")).not.toBeInTheDocument();
  });

  it("opens the shot editor for an episode whose script is generated", () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({
        episodes: [
          { episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "generated" },
        ],
      }),
      currentScripts: { "episode_1.json": makeScript() },
    });

    renderAt("/episodes/1");

    expect(screen.getByTestId("timeline-canvas")).toBeInTheDocument();
    expect(screen.getByTestId("timeline-has-script")).toHaveTextContent("yes");
    expect(screen.queryByTestId("episode-source-review")).not.toBeInTheDocument();
  });

  it("switches an episode with a script between the storyboard and edit views", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({
        episodes: [
          { episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "generated" },
        ],
      }),
      currentScripts: { "episode_1.json": makeScript() },
    });
    const { hook, searchHook } = memoryLocation({ path: "/episodes/1" });
    render(
      <Router hook={hook} searchHook={searchHook}>
        <StudioCanvasRouter />
      </Router>,
    );

    expect(screen.getByRole("tab", { name: "分镜" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("tab", { name: "剪辑" }));

    // Editing lazy-loads; first entry waits for chunks.
    expect(await screen.findByTestId("edit-timeline-view")).toHaveTextContent("episode 1");
    expect(screen.queryByTestId("timeline-canvas")).not.toBeInTheDocument();
    expect(screen.getByTestId("workflow-panel")).toBeInTheDocument();
    expect(screen.getByRole("tabpanel", { name: "剪辑" })).toContainElement(screen.getByTestId("edit-timeline-view"));

    fireEvent.click(screen.getByRole("tab", { name: "分镜" }));
    expect(screen.getByTestId("timeline-canvas")).toHaveAttribute("data-view", "board");
    expect(screen.getByRole("tabpanel", { name: "分镜" })).toContainElement(screen.getByTestId("timeline-canvas"));

    // Arrows move focus only; activation switches views because editing unmounts canvases. Tab stops only on selected views.
    const storyboard = screen.getByRole("tab", { name: "分镜" });
    expect(storyboard).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "剪辑" })).toHaveAttribute("tabindex", "-1");
    const user = userEvent.setup();
    act(() => storyboard.focus());
    await user.keyboard("{ArrowRight}");
    const edit = screen.getByRole("tab", { name: "剪辑" });
    expect(edit).toHaveFocus();
    expect(storyboard).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{Enter}");
    expect(screen.getByRole("tabpanel", { name: "剪辑" })).toContainElement(screen.getByTestId("edit-timeline-view"));
  });

  it("deep-links each canvas view through ?view= and writes no view for the default one", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({
        grid_storyboard: true,
        episodes: [
          { episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "generated" },
        ],
      }),
      currentScripts: { "episode_1.json": makeScript() },
    });
    const { hook, searchHook, history } = memoryLocation({ path: "/episodes/1?view=plan", record: true });
    render(
      <Router hook={hook} searchHook={searchHook}>
        <StudioCanvasRouter />
      </Router>,
    );

    expect(screen.getByRole("tab", { name: "脚本规划" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByTestId("grid-canvas")).toHaveAttribute("data-view", "plan");
    expect(screen.getByRole("tabpanel", { name: "脚本规划" })).toContainElement(screen.getByTestId("grid-canvas"));

    fireEvent.click(screen.getByRole("tab", { name: "多宫格分镜图" }));
    expect(screen.getByTestId("grid-canvas")).toHaveAttribute("data-view", "grid");
    expect(history.at(-1)).toBe("/episodes/1?view=grid");

    // Scripts default to storyboards without view parameters, so default pages automatically reach storyboards after generation.
    fireEvent.click(screen.getByRole("tab", { name: "分镜" }));
    expect(screen.getByTestId("grid-canvas")).toHaveAttribute("data-view", "board");
    expect(history.at(-1)).toBe("/episodes/1");
  });

  it("falls back to the default view when the linked one is unknown or not available yet", () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({
        episodes: [
          { episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "segmented" },
        ],
      }),
      currentScripts: {},
    });

    renderAt("/episodes/1?view=board");
    // With only intermediate plans, storyboards remain unavailable and planning selected.
    expect(screen.getByRole("tab", { name: "分镜" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("tab", { name: "脚本规划" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("timeline-canvas")).toHaveAttribute("data-view", "plan");
    cleanup();

    renderAt("/episodes/1?view=nonsense");
    expect(screen.getByTestId("timeline-canvas")).toHaveAttribute("data-view", "plan");
  });

  it("names the board view after video units on the reference route and opens its plan view from the link", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({
        generation_mode: "reference_video",
        episodes: [
          { episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "generated" },
        ],
      }),
      currentScripts: { "episode_1.json": makeScript() },
    });

    renderAt("/episodes/1?view=plan");

    expect(screen.getByRole("tab", { name: "视频单元" })).toHaveAttribute("aria-selected", "false");
    expect(screen.queryByRole("tab", { name: "分镜" })).not.toBeInTheDocument();
    expect(await screen.findByTestId("reference-video-canvas")).toHaveAttribute("data-view", "plan");
  });

  it("deletes the episode from the page header menu", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({
        episodes: [
          { episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "generated" },
        ],
      }),
      currentScripts: { "episode_1.json": makeScript() },
    });
    const impact = { episode: 1, recoverable: true, revision: "rev-1", text: "删除「EP1」后可以从集原文重新生成。" };
    const remove = vi
      .spyOn(API, "deleteEpisode")
      .mockResolvedValueOnce({ status: "confirmation_required", impact } as never)
      .mockResolvedValueOnce({ status: "deleted", impact } as never);
    useProjectsStore.setState({ refreshProject: vi.fn().mockResolvedValue("success") });
    const user = userEvent.setup();
    const { hook, searchHook, history } = memoryLocation({ path: "/episodes/1", record: true });
    render(
      <Router hook={hook} searchHook={searchHook}>
        <StudioCanvasRouter />
      </Router>,
    );

    await user.click(screen.getByRole("button", { name: "这一集的更多操作" }));
    await user.click(await screen.findByRole("menuitem", { name: "删除这一集" }));
    const dialog = await screen.findByRole("alertdialog", { name: "删除「EP1」" });
    await user.click(within(dialog).getByRole("button", { name: "移入项目回收站" }));

    // Deleting the viewed episode returns to the episode list.
    await waitFor(() => expect(history.at(-1)).toBe("/episodes"));
    expect(remove).toHaveBeenLastCalledWith("demo", 1, "rev-1");
  });

  it("opens the edit view directly from its link", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({
        episodes: [
          { episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "generated" },
        ],
      }),
      currentScripts: { "episode_1.json": makeScript() },
    });
    const { hook, searchHook } = memoryLocation({ path: episodeEditViewPath(1) });
    render(
      <Router hook={hook} searchHook={searchHook}>
        <StudioCanvasRouter />
      </Router>,
    );

    expect(screen.getByRole("tab", { name: "剪辑" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByTestId("edit-timeline-view")).toBeInTheDocument();
  });

  it("keeps the edit view closed for an episode that has no script yet", () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({
        episodes: [{ episode: 1, title: "EP1", script_file: "scripts/episode_1.json" }],
      }),
      currentScripts: {},
    });
    const { hook, searchHook } = memoryLocation({ path: episodeEditViewPath(1) });
    render(
      <Router hook={hook} searchHook={searchHook}>
        <StudioCanvasRouter />
      </Router>,
    );

    expect(screen.queryByRole("tab", { name: "剪辑" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("edit-timeline-view")).not.toBeInTheDocument();
  });

  it("submits character generation, marks the character active and confirms with a toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateCharacter").mockResolvedValue({ success: true, task_id: "t-1", deduped: false, message: "已提交" });

    renderAt("/characters");

    fireEvent.click(screen.getByText("generate-character"));
    await waitFor(() => {
      expect(API.generateCharacter).toHaveBeenCalledWith("demo", "Hero");
      expect(useAppStore.getState().toast?.text).toContain("生成任务已提交");
      expect(useAppStore.getState().toast?.tone).toBe("success");
      // Enqueue success immediately marks characters occupied, closing the window before SSE polling
      // could falsely allow concurrent image_edit (tasks-store.ts optimistic occupancy).
      const { tasks, optimisticActive } = useTasksStore.getState();
      expect(selectActiveResourceIds(tasks, "character", "demo", optimisticActive).has("Hero")).toBe(true);
    });
  });

  it("reports scene and prop generation failures with toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateProjectScene").mockRejectedValue(new Error("scene generate failed"));
    vi.spyOn(API, "generateProjectProp").mockRejectedValue(new Error("prop generate failed"));

    const scenes = renderAt("/scenes");
    fireEvent.click(screen.getByText("generate-scene"));
    await waitFor(() => {
      expect(API.generateProjectScene).toHaveBeenCalledWith("demo", "Temple");
      expect(useAppStore.getState().toast?.text).toContain("提交失败");
    });
    scenes.unmount();
    useAppStore.setState({ toast: null });

    renderAt("/props");
    fireEvent.click(screen.getByText("generate-prop"));
    await waitFor(() => {
      expect(API.generateProjectProp).toHaveBeenCalledWith("demo", "Sword");
      expect(useAppStore.getState().toast?.text).toContain("提交失败");
    });
  });

  it("marks scene generation as optimistically active on submit success", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateProjectScene").mockResolvedValue({ success: true, task_id: "t-1", deduped: false, message: "已提交" });

    renderAt("/scenes");
    fireEvent.click(screen.getByText("generate-scene"));
    await waitFor(() => {
      expect(API.generateProjectScene).toHaveBeenCalledWith("demo", "Temple");
      const { tasks, optimisticActive } = useTasksStore.getState();
      expect(selectActiveResourceIds(tasks, "scene", "demo", optimisticActive).has("Temple")).toBe(true);
    });
  });

  it("marks prop generation as optimistically active on submit success", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateProjectProp").mockResolvedValue({ success: true, task_id: "t-1", deduped: false, message: "已提交" });

    renderAt("/props");
    fireEvent.click(screen.getByText("generate-prop"));
    await waitFor(() => {
      expect(API.generateProjectProp).toHaveBeenCalledWith("demo", "Sword");
      const { tasks, optimisticActive } = useTasksStore.getState();
      expect(selectActiveResourceIds(tasks, "prop", "demo", optimisticActive).has("Sword")).toBe(true);
    });
  });

  it("submits product generation and reports its failure with toast", async () => {
    const projectData = makeProjectData({
      products: { Phone: { description: "sleek phone" } },
    });
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: projectData,
      currentScripts: { "episode_1.json": makeScript() },
    });
    const generateSpy = vi
      .spyOn(API, "generateProjectProduct")
      .mockResolvedValueOnce({ success: true, task_id: "t-1", deduped: false, message: "已提交" })
      .mockRejectedValueOnce(new Error("product generate failed"));

    renderAt("/products");
    expect(screen.getByTestId("product-gallery")).toHaveAttribute("data-names", "Phone");

    fireEvent.click(screen.getByText("generate-product"));
    await waitFor(() => {
      expect(generateSpy).toHaveBeenCalledWith("demo", "Phone");
      expect(useAppStore.getState().toast?.text).toContain("资产图生成任务已提交");
      expect(useAppStore.getState().toast?.tone).toBe("success");
      const { tasks, optimisticActive } = useTasksStore.getState();
      expect(selectActiveResourceIds(tasks, "product", "demo", optimisticActive).has("Phone")).toBe(true);
    });

    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
    fireEvent.click(screen.getByText("generate-product"));
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toContain("提交失败");
    });
  });

  it("runs timeline callbacks and handles generation failures", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "updateSegment").mockRejectedValue(new Error("update failed"));
    vi.spyOn(API, "generateStoryboard").mockRejectedValue(new Error("storyboard failed"));
    vi.spyOn(API, "generateVideo").mockRejectedValue(new Error("video failed"));

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("update-prompt"));
    await waitFor(() => {
      expect(API.updateSegment).toHaveBeenCalledWith("demo", "SEG-1", {
        script_file: "episode_1.json",
        image_prompt: "new prompt",
      });
      // Propagate save failures to shot-detail notices, without extra global feedback.
      expect(screen.getByText("update-prompt")).toHaveAttribute("data-error", "update failed");
    });
    expect(useAppStore.getState().toast).toBeNull();

    fireEvent.click(screen.getByText("generate-storyboard"));
    await waitFor(() => {
      expect(API.generateStoryboard).toHaveBeenCalledWith(
        "demo",
        "SEG-1",
        "image prompt",
        "episode_1.json",
      );
      expect(useAppStore.getState().toast?.text).toContain("生成分镜失败");
    });

    fireEvent.click(screen.getByText("generate-video"));
    await waitFor(() => {
      expect(API.generateVideo).toHaveBeenCalledWith(
        "demo",
        "SEG-1",
        "video prompt",
        "episode_1.json",
        4,
      );
      expect(useAppStore.getState().toast?.text).toContain("生成视频失败");
    });
  });

  // TimelineCanvas ShotDetail.handleSave / handleRefsSave await this callback to maintain saving
  // state. Wrapping the real-result handleUpdatePrompt in voidPromise discards returns and resolves
  // wrappers immediately, ending await before PATCH persistence.
  it("keeps the update-prompt callback pending until the underlying write settles", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    let resolveUpdate: (result: { success: boolean }) => void = () => {};
    vi.spyOn(API, "updateSegment").mockReturnValue(
      new Promise((resolve) => {
        resolveUpdate = resolve;
      }),
    );

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("update-prompt-await"));

    // Flush several micro/macro tasks while PATCH remains pending; callbacks must not resolve early.
    // voidPromise wrappers would already resolve here, concealing the actual bug.
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByText("update-prompt-await")).toHaveAttribute("data-update-pending", "true");

    resolveUpdate({ success: true });

    await waitFor(() => {
      expect(screen.getByText("update-prompt-await")).toHaveAttribute(
        "data-update-pending",
        "false",
      );
    });
  });

  it("resolves ad shots by shot_id when generating storyboard and video", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ content_mode: "ad" }),
      currentScripts: { "episode_1.json": makeAdScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ content_mode: "ad" }),
      scripts: { "episode_1.json": makeAdScript() },
    });
    vi.spyOn(API, "generateStoryboard").mockResolvedValue({
      success: true,
      task_id: "t-sb",
      deduped: false,
      message: "已提交",
    });
    vi.spyOn(API, "generateVideo").mockResolvedValue({
      success: true,
      task_id: "t-v",
      deduped: false,
      message: "已提交",
    });

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("generate-storyboard"));
    await waitFor(() => {
      expect(API.generateStoryboard).toHaveBeenCalledWith(
        "demo",
        "SEG-1",
        "ad image prompt",
        "episode_1.json",
      );
      expect(useAppStore.getState().toast?.tone).toBe("success");
    });

    fireEvent.click(screen.getByText("generate-video"));
    await waitFor(() => {
      // duration uses shot duration_seconds(5), never default 4.
      expect(API.generateVideo).toHaveBeenCalledWith(
        "demo",
        "SEG-1",
        "ad video prompt",
        "episode_1.json",
        5,
      );
    });
  });

  it("dispatches ad prompt updates to the shot PATCH endpoint", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ content_mode: "ad" }),
      currentScripts: { "episode_1.json": makeAdScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ content_mode: "ad" }),
      scripts: { "episode_1.json": makeAdScript() },
    });
    const updateShotSpy = vi.spyOn(API, "updateShot").mockResolvedValue({ success: true });
    const updateSceneSpy = vi.spyOn(API, "updateScene").mockResolvedValue({ success: true });
    const updateSegmentSpy = vi.spyOn(API, "updateSegment").mockResolvedValue({ success: true });

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("update-prompt"));
    await waitFor(() => {
      expect(updateShotSpy).toHaveBeenCalledWith("demo", "SEG-1", "episode_1.json", {
        image_prompt: "new prompt",
      });
    });
    expect(updateSceneSpy).not.toHaveBeenCalled();
    expect(updateSegmentSpy).not.toHaveBeenCalled();
  });

  it("moves a shot after its anchor", async () => {
    const script = makeAdScript() as AdEpisodeScript;
    script.shots.push({
      shot_id: "SEG-2",
      section: "cta",
      duration_seconds: 3,
      voiceover_text: "立即下单",
      image_prompt: "p2",
      video_prompt: "v2",
    });
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ content_mode: "ad" }),
      currentScripts: { "episode_1.json": script },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ content_mode: "ad" }),
      scripts: { "episode_1.json": script },
    });
    const moveSpy = vi.spyOn(API, "moveScriptItem").mockResolvedValue({ success: true });

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("move-shot-later"));
    await waitFor(() => {
      expect(moveSpy).toHaveBeenCalledWith("demo", "episode_1.json", "SEG-1", "SEG-2");
    });
    // Reorder plus local refresh succeed → report move success.
    await waitFor(() => {
      expect(screen.getByText("move-shot-later")).toHaveAttribute("data-move-result", "true");
    });
  });

  it("reports move failure and toasts when the move request fails", async () => {
    const script = makeAdScript() as AdEpisodeScript;
    script.shots.push({
      shot_id: "SEG-2",
      section: "cta",
      duration_seconds: 3,
      voiceover_text: "立即下单",
      image_prompt: "p2",
      video_prompt: "v2",
    });
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ content_mode: "ad" }),
      currentScripts: { "episode_1.json": script },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ content_mode: "ad" }),
      scripts: { "episode_1.json": script },
    });
    vi.spyOn(API, "moveScriptItem").mockRejectedValue(new Error("server boom"));

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("move-shot-later"));
    await waitFor(() => {
      expect(screen.getByText("move-shot-later")).toHaveAttribute("data-move-result", "false");
    });
    expect(useAppStore.getState().toast?.text).toContain("server boom");
    expect(useAppStore.getState().toast?.tone).toBe("error");
  });

  it("reports move failure when local refresh fails after a successful move", async () => {
    const script = makeAdScript() as AdEpisodeScript;
    script.shots.push({
      shot_id: "SEG-2",
      section: "cta",
      duration_seconds: 3,
      voiceover_text: "立即下单",
      image_prompt: "p2",
      video_prompt: "v2",
    });
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ content_mode: "ad" }),
      currentScripts: { "episode_1.json": script },
    });

    // Move API succeeds but project refresh fails, retaining old local segments;
    // report failure or callers advance selectedIndex to wrong shots.
    vi.spyOn(API, "getProject").mockRejectedValue(new Error("network down"));
    vi.spyOn(API, "moveScriptItem").mockResolvedValue({ success: true });

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("move-shot-later"));
    await waitFor(() => {
      expect(screen.getByText("move-shot-later")).toHaveAttribute("data-move-result", "false");
    });
  });

  it("warns when the local refresh fails after a successful move", async () => {
    const script = makeAdScript() as AdEpisodeScript;
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ content_mode: "ad" }),
      currentScripts: { "episode_1.json": script },
    });
    vi.spyOn(API, "getProject").mockRejectedValue(new Error("network down"));
    vi.spyOn(API, "moveScriptItem").mockResolvedValue({ success: true });

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("move-shot-later"));
    await waitFor(() => expect(screen.getByText("move-shot-later")).toHaveAttribute("data-move-result", "false"));
    expect(useAppStore.getState().toast).toMatchObject({ text: REFRESH_FAILED, tone: "warning" });
  });

  it.each([
    ["insert-shot", "insertScriptItem"],
    ["remove-shot", "removeScriptItem"],
  ] as const)(
    "%s reports success once the edit is committed even if the local refresh then fails",
    async (button, apiMethod) => {
      const script = makeAdScript() as AdEpisodeScript;
      useProjectsStore.setState({
        currentProjectName: "demo",
        currentProjectData: makeProjectData({ content_mode: "ad" }),
        currentScripts: { "episode_1.json": script },
      });
      vi.spyOn(API, "getProject").mockRejectedValue(new Error("network down"));
      const editSpy = vi.spyOn(API, apiMethod).mockResolvedValue({ success: true } as never);

      renderAt("/episodes/1");

      fireEvent.click(screen.getByText(button));
      await waitFor(() => expect(screen.getByText(button)).toHaveAttribute("data-result", "true"));
      expect(editSpy).toHaveBeenCalledTimes(1);
      expect(useAppStore.getState().toast?.tone).toBe("warning");
    },
  );

  it("routes ad + reference_video projects to the unified unit canvas without preprocessing", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({
        content_mode: "ad",
        generation_mode: "reference_video",
      }),
      currentScripts: { "episode_1.json": makeAdScript() },
    });
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ content_mode: "ad", generation_mode: "reference_video" }),
      scripts: { "episode_1.json": makeAdScript() },
    });
    vi.spyOn(API, "updateEpisode").mockResolvedValue({ success: true });

    renderAt("/episodes/1");

    const canvas = await screen.findByTestId("reference-video-canvas");
    expect(canvas).toHaveAttribute("data-has-script", "yes");
    expect(canvas).toHaveAttribute("data-preprocess", "no");
    expect(canvas).toHaveAttribute("data-free-duration", "yes");
    // Shot-editing canvas no longer renders under this path.
    expect(screen.queryByTestId("timeline-canvas")).not.toBeInTheDocument();
    // Ads/short films lack script planning, label storyboards video units, and forbid deleting their sole episode.
    expect(screen.queryByRole("tab", { name: "脚本规划" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "视频单元" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("button", { name: "这一集的更多操作" })).not.toBeInTheDocument();

    await renameEpisode("新标题");
    await waitFor(() => {
      expect(API.updateEpisode).toHaveBeenCalledWith("demo", 1, { title: "新标题" });
    });
  });

  it("regenerates against the script file of the episode being viewed, not the first one loaded", async () => {
    // Multiepisode currentScripts includes all scripts. Taking its first key for episode-2 regeneration
    // would redo episode-1 already-paid artifacts instead.
    const episode2 = { ...makeScript(), episode: 2, title: "EP2" };
    const projectData = makeProjectData({
      episodes: [
        { episode: 1, title: "EP1", script_file: "scripts/episode_1.json" },
        { episode: 2, title: "EP2", script_file: "scripts/episode_2.json" },
      ],
    });
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: projectData,
      currentScripts: { "episode_1.json": makeScript(), "episode_2.json": episode2 },
    });
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: projectData,
      scripts: { "episode_1.json": makeScript(), "episode_2.json": episode2 },
    });
    vi.spyOn(API, "generateStoryboard").mockResolvedValue({
      success: true,
      task_id: "t-sb",
      deduped: false,
      message: "已提交",
    });

    renderAt("/episodes/2");

    fireEvent.click(screen.getByText("workflow-regenerate"));
    await waitFor(() => {
      expect(API.generateStoryboard).toHaveBeenCalledWith(
        "demo",
        "SEG-1",
        "image prompt",
        "episode_2.json",
      );
    });
  });

  it("routes the panel's video regenerate through the storyboard video entry", async () => {
    const projectData = makeProjectData();
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: projectData,
      currentScripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: projectData,
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateVideo").mockRejectedValue(new Error("provider down"));

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("workflow-regenerate-video"));
    await waitFor(() => {
      expect(useAppStore.getState().toast?.tone).toBe("error");
      expect(useAppStore.getState().toast?.text).toBe("生成视频失败：provider down");
    });
    expect(API.generateVideo).toHaveBeenCalledWith("demo", "SEG-1", "video prompt", "episode_1.json", 4);
  });

  it("withholds the panel's regenerate entry on the reference route instead of wiring a dead button", async () => {
    // Reference-to-video uses video_units; these per-unit callbacks cannot resolve prompts. Supplying them
    // creates inert buttons; regeneration belongs on unit cards.
    const projectData = makeProjectData({ generation_mode: "reference_video" });
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: projectData,
      currentScripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: projectData,
      scripts: { "episode_1.json": makeScript() },
    });

    renderAt("/episodes/1");

    expect(await screen.findByTestId("workflow-panel")).toHaveAttribute("data-can-regenerate", "no");
  });

  it("uses the unified unit canvas even when project and script content modes temporarily differ", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({
        content_mode: "ad",
        generation_mode: "reference_video",
      }),
      currentScripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ content_mode: "ad", generation_mode: "reference_video" }),
      scripts: { "episode_1.json": makeScript() },
    });

    renderAt("/episodes/1");

    const canvas = await screen.findByTestId("reference-video-canvas");
    expect(canvas).toHaveAttribute("data-has-script", "yes");
    expect(canvas).toHaveAttribute("data-preprocess", "no");
  });

  it("keeps ad + storyboard projects on the shot editor", () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ content_mode: "ad" }),
      currentScripts: { "episode_1.json": makeAdScript() },
    });
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ content_mode: "ad" }),
      scripts: { "episode_1.json": makeAdScript() },
    });

    renderAt("/episodes/1");

    expect(screen.getByTestId("timeline-canvas")).toBeInTheDocument();
    expect(screen.queryByTestId("reference-video-canvas")).not.toBeInTheDocument();
  });

  it("resolves drama scenes by scene_id when generating storyboard", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ content_mode: "drama" }),
      currentScripts: { "episode_1.json": makeDramaScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ content_mode: "drama" }),
      scripts: { "episode_1.json": makeDramaScript() },
    });
    vi.spyOn(API, "generateStoryboard").mockResolvedValue({
      success: true,
      task_id: "t-sb",
      deduped: false,
      message: "已提交",
    });

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("generate-storyboard"));
    await waitFor(() => {
      expect(API.generateStoryboard).toHaveBeenCalledWith(
        "demo",
        "SEG-1",
        "drama image prompt",
        "episode_1.json",
      );
      const { tasks, optimisticActive } = useTasksStore.getState();
      expect(selectActiveResourceIds(tasks, "storyboard", "demo", optimisticActive).has("SEG-1")).toBe(true);
    });
  });

  it("reports character generation failure with an error toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateCharacter").mockRejectedValue(new Error("character generate failed"));

    renderAt("/characters");

    fireEvent.click(screen.getByText("generate-character"));
    await waitFor(() => {
      expect(API.generateCharacter).toHaveBeenCalledWith("demo", "Hero");
      expect(useAppStore.getState().toast?.text).toContain("提交失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });
  });

  it("saves the episode title from the page header without a success toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "updateEpisode").mockResolvedValue({ success: true });

    renderAt("/episodes/1");

    await renameEpisode("新标题");
    await waitFor(() => {
      expect(API.updateEpisode).toHaveBeenCalledWith("demo", 1, { title: "新标题" });
      expect(API.getProject).toHaveBeenCalled();
    });
    // Immediate successful edits show no toasts; titles return to display.
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "编辑分集标题" })).not.toBeInTheDocument());
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("leaves the edit state and warns when the refresh after saving the title fails", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "getProject").mockRejectedValue(new Error("network down"));
    vi.spyOn(API, "updateEpisode").mockResolvedValue({ success: true });

    renderAt("/episodes/1");

    await renameEpisode("新标题");
    await waitFor(() =>
      expect(useAppStore.getState().toast).toMatchObject({ text: REFRESH_FAILED, tone: "warning" }),
    );
    // Saved titles exit editing to avoid encouraging duplicate submissions.
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "编辑分集标题" })).not.toBeInTheDocument());
  });

  it.each(["restore-character", "reload-character"])(
    "%s warns and reports not-synced when the refresh after the write fails",
    async (button) => {
      useProjectsStore.setState({
        currentProjectName: "demo",
        currentProjectData: makeProjectData(),
        currentScripts: { "episode_1.json": makeScript() },
      });
      vi.spyOn(API, "getProject").mockRejectedValue(new Error("network down"));

      renderAt("/characters");

      fireEvent.click(screen.getByText(button));
      await waitFor(() => expect(screen.getByText(button)).toHaveAttribute("data-result", "false"));
      expect(useAppStore.getState().toast).toMatchObject({ text: REFRESH_FAILED, tone: "warning" });
    },
  );

  it("drops an unsaved title draft when switching to another episode", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({
        episodes: [
          { episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "generated" },
          { episode: 2, title: "EP2", script_file: "scripts/episode_2.json", script_status: "generated" },
        ],
      }),
      currentScripts: { "episode_1.json": makeScript(), "episode_2.json": makeScript() },
    });
    const { hook, searchHook, navigate } = memoryLocation({ path: "/episodes/1" });
    render(
      <Router hook={hook} searchHook={searchHook}>
        <StudioCanvasRouter />
      </Router>,
    );

    fireEvent.click(screen.getByRole("button", { name: "编辑分集标题" }));
    fireEvent.change(screen.getByRole("textbox", { name: "编辑分集标题" }), { target: { value: "写给第一集的标题" } });
    act(() => navigate("/episodes/2"));

    expect(screen.queryByRole("textbox", { name: "编辑分集标题" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "EP2" })).toBeInTheDocument();
  });

  it("reports episode title update failure with an error toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "updateEpisode").mockRejectedValue(new Error("episode title failed"));

    renderAt("/episodes/1");

    await renameEpisode("新标题");
    await waitFor(() => {
      expect(API.updateEpisode).toHaveBeenCalledWith("demo", 1, { title: "新标题" });
      expect(useAppStore.getState().toast?.text).toContain("更新分集标题失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });
    // Failures retain editing/input.
    expect(screen.getByRole("textbox", { name: "编辑分集标题" })).toHaveValue("新标题");
  });

  it("hides narration generation entries for post-production projects", () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ narration_delivery: "post_production" }),
      currentScripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ narration_delivery: "post_production" }),
      scripts: { "episode_1.json": makeScript() },
    });

    renderAt("/episodes/1");

    expect(screen.queryByText("generate-narration")).not.toBeInTheDocument();
    expect(screen.queryByText("generate-episode-narration")).not.toBeInTheDocument();
  });

  it("submits narration generation and shows a success toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ narration_delivery: "use_tts" }),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ narration_delivery: "use_tts" }),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateNarrationAudio").mockResolvedValue({
      success: true,
      task_id: "t-1",
      deduped: false,
      message: "已提交",
    });

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("generate-narration"));
    await waitFor(() => {
      expect(API.generateNarrationAudio).toHaveBeenCalledWith("demo", "SEG-1", "episode_1.json");
      expect(useAppStore.getState().toast?.text).toContain("旁白配音");
      expect(useAppStore.getState().toast?.tone).toBe("success");
    });
  });

  it("reports narration generation failure with an error toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ narration_delivery: "use_tts" }),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ narration_delivery: "use_tts" }),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateNarrationAudio").mockRejectedValue(new Error("tts failed"));

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("generate-narration"));
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toContain("生成旁白配音失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });
  });

  it("submits episode narration batch and reports the submitted count", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ narration_delivery: "use_tts" }),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ narration_delivery: "use_tts" }),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateEpisodeNarrationAudio").mockResolvedValue({
      success: true,
      task_ids: ["t-1", "t-2"],
      task_ids_by_segment: { "seg-1": "t-1", "seg-2": "t-2" },
      deduped: false,
      message: "已提交",
    });

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("generate-episode-narration"));
    await waitFor(() => {
      expect(API.generateEpisodeNarrationAudio).toHaveBeenCalledWith("demo", "episode_1.json");
      expect(useAppStore.getState().toast?.text).toContain("已提交 2 个旁白配音生成任务");
      expect(useAppStore.getState().toast?.tone).toBe("success");
    });
  });

  it("tells the user when episode narration has nothing missing", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ narration_delivery: "use_tts" }),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ narration_delivery: "use_tts" }),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateEpisodeNarrationAudio").mockResolvedValue({
      success: true,
      task_ids: [],
      task_ids_by_segment: {},
      deduped: false,
      message: "无需补缺",
    });

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("generate-episode-narration"));
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toContain("所有分镜均已生成旁白配音");
      expect(useAppStore.getState().toast?.tone).toBe("success");
    });
  });

  it("blocks narration generation when no audio provider is configured", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ narration_delivery: "use_tts" }),
      currentScripts: { "episode_1.json": makeScript() },
    });
    useConfigStatusStore.setState({
      initialized: true,
      availableMediaTypes: ["image", "video", "text"],
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ narration_delivery: "use_tts" }),
      scripts: { "episode_1.json": makeScript() },
    });
    const generateSpy = vi.spyOn(API, "generateNarrationAudio");

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("generate-narration"));
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toContain("音频供应商");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });
    expect(generateSpy).not.toHaveBeenCalled();
  });

  it("reports grid generation failure with an error toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ generation_mode: "storyboard", grid_storyboard: true }),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ generation_mode: "storyboard", grid_storyboard: true }),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateGrid").mockRejectedValue(new Error("grid generate failed"));

    renderAt("/episodes/1");

    fireEvent.click(await screen.findByText("generate-grid"));
    await waitFor(() => {
      expect(API.generateGrid).toHaveBeenCalledWith("demo", 1, "episode_1.json", undefined);
      expect(useAppStore.getState().toast?.text).toContain("多宫格分镜生成失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });
  });

  it("submits the grid video without any narration delivery choice", async () => {
    const projectData = makeProjectData({ generation_mode: "storyboard", grid_storyboard: true });
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: projectData,
      currentScripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: projectData,
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateVideo").mockResolvedValue({
      success: true,
      task_id: "t-v",
      deduped: false,
      message: "已提交",
    });

    renderAt("/episodes/1");

    const button = await screen.findByText("generate-grid-video-await");
    fireEvent.click(button);

    await waitFor(() => {
      expect(button).toHaveAttribute("data-video-result", "resolved");
    });
    expect(API.generateVideo).toHaveBeenCalledWith(
      "demo",
      "SEG-1",
      "video prompt",
      "episode_1.json",
      4,
    );
  });

  it("marks the scriptFile as optimistically active on grid generation submit success", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ generation_mode: "storyboard", grid_storyboard: true }),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ generation_mode: "storyboard", grid_storyboard: true }),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateGrid").mockResolvedValue({
      success: true,
      grid_ids: ["grid-1"],
      task_ids: ["t-1"],
      task_ids_by_grid: { "grid-1": "t-1" },
      unsplit_grid_ids: [],
      deduped: false,
      message: "已提交",
    });

    renderAt("/episodes/1");

    fireEvent.click(await screen.findByText("generate-grid"));
    await waitFor(() => {
      expect(API.generateGrid).toHaveBeenCalledWith("demo", 1, "episode_1.json", undefined);
      const { tasks, optimisticActiveScriptFile } = useTasksStore.getState();
      expect(
        selectHasActiveTaskForScriptFile(tasks, "grid", "episode_1.json", "demo", optimisticActiveScriptFile),
      ).toBe(true);
    });
  });

  it("does not mark optimistic occupancy when grid generation returns no task_ids", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ generation_mode: "storyboard", grid_storyboard: true }),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ generation_mode: "storyboard", grid_storyboard: true }),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateGrid").mockResolvedValue({
      success: true,
      grid_ids: [],
      task_ids: [],
      task_ids_by_grid: {},
      unsplit_grid_ids: [],
      deduped: false,
      message: "已提交 0 个多宫格分镜生成任务",
    });

    renderAt("/episodes/1");

    fireEvent.click(await screen.findByText("generate-grid"));
    await waitFor(() => {
      expect(API.generateGrid).toHaveBeenCalledWith("demo", 1, "episode_1.json", undefined);
      expect(useAppStore.getState().toast?.text).toBe("已提交 0 个多宫格分镜生成任务");
    });

    const { tasks, optimisticActiveScriptFile } = useTasksStore.getState();
    expect(
      selectHasActiveTaskForScriptFile(tasks, "grid", "episode_1.json", "demo", optimisticActiveScriptFile),
    ).toBe(false);
  });
});
