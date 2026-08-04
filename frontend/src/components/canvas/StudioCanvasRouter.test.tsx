import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useProjectsStore } from "@/stores/projects-store";
import { selectActiveResourceIds, selectHasActiveTaskForScriptFile, useTasksStore } from "@/stores/tasks-store";
import { StudioCanvasRouter } from "@/components/canvas/StudioCanvasRouter";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";
import type { AdEpisodeScript, EpisodeScript, ProjectData } from "@/types";

vi.mock("./OverviewCanvas", () => ({
  OverviewCanvas: () => <div data-testid="overview-canvas">Overview</div>,
}));

vi.mock("./SourceFileViewer", () => ({
  SourceFileViewer: ({ filename }: { filename: string }) => (
    <div data-testid="source-file-viewer">{filename}</div>
  ),
}));

vi.mock("./timeline/TimelineCanvas", () => ({
  TimelineCanvas: ({
    episodeScript,
    scriptFile,
    durationOptions,
    onUpdatePrompt,
    onMoveShot,
    onGenerateStoryboard,
    onGenerateVideo,
    onGenerateNarration,
    onGenerateEpisodeNarration,
    onSaveTitle,
    canEditTitle,
  }: {
    episodeScript: unknown;
    scriptFile?: string;
    durationOptions?: number[];
    onUpdatePrompt?: (segmentId: string, field: string, value: unknown, scriptFile?: string) => void;
    onMoveShot?: (
      shotId: string,
      direction: "earlier" | "later",
      scriptFile?: string,
    ) => Promise<boolean> | void;
    onGenerateStoryboard?: (segmentId: string) => void;
    onGenerateVideo?: (segmentId: string) => void;
    onGenerateNarration?: (segmentId: string) => void;
    onGenerateEpisodeNarration?: (scriptFile?: string) => void;
    onSaveTitle?: (title: string) => Promise<void>;
    canEditTitle?: boolean;
  }) => (
    <div data-testid="timeline-canvas">
      <div data-testid="timeline-has-script">{episodeScript ? "yes" : "no"}</div>
      <div data-testid="timeline-can-edit-title">{canEditTitle ? "yes" : "no"}</div>
      <div data-testid="timeline-duration-options">{(durationOptions ?? []).join(",")}</div>
      <button onClick={() => onUpdatePrompt?.("SEG-1", "image_prompt", "new prompt", scriptFile)}>
        update-prompt
      </button>
      <button
        onClick={(e) => {
          const el = e.currentTarget;
          el.setAttribute("data-update-pending", "true");
          void Promise.resolve(
            onUpdatePrompt?.("SEG-1", "image_prompt", "new prompt", scriptFile),
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
          void Promise.resolve(onMoveShot?.("SEG-1", "later", scriptFile)).then((moved) => {
            el.setAttribute("data-move-result", String(moved));
          });
        }}
      >
        move-shot-later
      </button>
      <button onClick={() => onGenerateStoryboard?.("SEG-1")}>generate-storyboard</button>
      <button onClick={() => onGenerateVideo?.("SEG-1")}>generate-video</button>
      <button onClick={() => onGenerateNarration?.("SEG-1")}>generate-narration</button>
      <button onClick={() => onGenerateEpisodeNarration?.()}>generate-episode-narration</button>
      <button onClick={() => void onSaveTitle?.("新标题")?.catch(() => {})}>save-title</button>
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

vi.mock("./reference/AdReferenceVideoCanvas", () => ({
  AdReferenceVideoCanvas: ({
    shots,
    hasScript,
    canEditTitle,
    onSaveTitle,
    onUpdatePrompt,
  }: {
    shots: { shot_id: string }[];
    hasScript: boolean;
    canEditTitle?: boolean;
    onSaveTitle?: (title: string) => Promise<void>;
    onUpdatePrompt?: (...args: unknown[]) => Promise<boolean> | void;
  }) => (
    <div
      data-testid="ad-reference-canvas"
      data-has-script={hasScript ? "yes" : "no"}
      data-editable={onUpdatePrompt ? "yes" : "no"}
    >
      <div data-testid="ad-reference-can-edit-title">{canEditTitle ? "yes" : "no"}</div>
      {shots.map((s) => s.shot_id).join(",")}
      <button onClick={() => void onSaveTitle?.("新标题")?.catch(() => {})}>
        ad-reference-save-title
      </button>
      <button
        onClick={(e) => {
          const el = e.currentTarget;
          void Promise.resolve(
            onUpdatePrompt?.("SEG-1", { duration_seconds: 7 }, undefined, "episode_1.json"),
          ).then((result) => {
            el.setAttribute("data-update-result", String(result));
          });
        }}
      >
        ad-reference-update-prompt
      </button>
    </div>
  ),
}));

vi.mock("./grid/GridImageToVideoCanvas", () => ({
  GridImageToVideoCanvas: ({
    onGenerateGrid,
  }: {
    onGenerateGrid?: (
      episode: number,
      scriptFile: string,
      sceneIds?: string[],
    ) => void | Promise<void>;
  }) => (
    <div data-testid="grid-canvas">
      <button onClick={() => void onGenerateGrid?.(1, "episode_1.json")}>generate-grid</button>
    </div>
  ),
}));

vi.mock("./lorebook/CharacterCard", () => ({
  CharacterCard: ({
    name,
    onSave,
    onGenerate,
  }: {
    name: string;
    onSave: (
      name: string,
      payload: { description: string; voiceStyle: string; referenceFile?: File | null; audioFile?: File | null },
    ) => Promise<void>;
    onGenerate: (name: string) => void;
  }) => (
    <div data-testid="character-card" data-name={name}>
      <button
        onClick={() =>
          void onSave(name, {
            description: "new desc",
            voiceStyle: "new voice",
            referenceFile: new File(["ref"], "hero.png", { type: "image/png" }),
          })
        }
      >
        update-character
      </button>
      <button
        onClick={() =>
          void onSave(name, {
            description: "new desc",
            voiceStyle: "new voice",
            audioFile: new File(["audio"], "hero.wav", { type: "audio/wav" }),
          })
        }
      >
        update-character-with-audio
      </button>
      <button onClick={() => onGenerate(name)}>generate-character</button>
    </div>
  ),
}));

vi.mock("./lorebook/SceneCard", () => ({
  SceneCard: ({
    name,
    onUpdate,
    onGenerate,
  }: {
    name: string;
    onUpdate: (name: string, updates: Record<string, unknown>) => void;
    onGenerate: (name: string) => void;
  }) => (
    <div data-testid="scene-card" data-name={name}>
      <button onClick={() => onUpdate(name, { description: "new scene desc" })}>
        update-scene
      </button>
      <button onClick={() => onGenerate(name)}>generate-scene</button>
    </div>
  ),
}));

vi.mock("./lorebook/PropCard", () => ({
  PropCard: ({
    name,
    onUpdate,
    onGenerate,
  }: {
    name: string;
    onUpdate: (name: string, updates: Record<string, unknown>) => void;
    onGenerate: (name: string) => void;
  }) => (
    <div data-testid="prop-card" data-name={name}>
      <button onClick={() => onUpdate(name, { description: "new prop desc" })}>
        update-prop
      </button>
      <button onClick={() => onGenerate(name)}>generate-prop</button>
    </div>
  ),
}));

vi.mock("./lorebook/ProductsPage", () => ({
  ProductsPage: ({
    products,
    onUpdateProduct,
    onGenerateProduct,
    onAddProduct,
  }: {
    products: Record<string, { description: string }>;
    onUpdateProduct: (name: string, updates: Record<string, unknown>) => void;
    onGenerateProduct: (name: string) => void;
    onAddProduct: (name: string, description: string, brand: string) => Promise<void>;
  }) => (
    <div data-testid="products-page" data-names={Object.keys(products).join(",")}>
      <button onClick={() => onUpdateProduct("Phone", { description: "new product desc" })}>
        update-product
      </button>
      <button onClick={() => onGenerateProduct("Phone")}>generate-product</button>
      <button onClick={() => void onAddProduct("NewPhone", "desc", "Acme").catch(() => {})}>
        add-product
      </button>
      <button onClick={() => void onAddProduct("NewPhone", "desc", "").catch(() => {})}>
        add-product-no-brand
      </button>
    </div>
  ),
}));

vi.mock("./lorebook/AddCharacterForm", () => ({
  AddCharacterForm: ({
    onSubmit,
    onCancel,
  }: {
    onSubmit: (
      name: string,
      description: string,
      voice: string,
      referenceFile?: File | null,
    ) => Promise<void>;
    onCancel: () => void;
  }) => (
    <div data-testid="add-character-form">
      <button
        onClick={() =>
          void onSubmit(
            "NewHero",
            "desc",
            "voice",
            new File(["ref"], "new-hero.png", { type: "image/png" }),
          )
        }
      >
        submit-add-character
      </button>
      <button onClick={onCancel}>cancel-add-character</button>
    </div>
  ),
}));

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
    duration_seconds: 4,
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
        transition_to_next: "cut",
      },
    ],
  };
}

function makeAdScript(): EpisodeScript {
  return {
    episode: 1,
    title: "广告视频",
    content_mode: "ad",
    duration_seconds: 30,
    novel: { title: "n", chapter: "1" },
    shots: [
      {
        shot_id: "SEG-1",
        section: "hook",
        duration_seconds: 5,
        voiceover_text: "口播文案",
        image_prompt: "ad image prompt",
        video_prompt: "ad video prompt",
        transition_to_next: "cut",
      },
    ],
  };
}

function makeDramaScript(): EpisodeScript {
  return {
    episode: 1,
    title: "EP1",
    content_mode: "drama",
    duration_seconds: 6,
    novel: { title: "n", chapter: "1" },
    scenes: [
      {
        scene_id: "SEG-1",
        duration_seconds: 6,
        segment_break: false,
        characters_in_scene: ["Hero"],
        image_prompt: "drama image prompt",
        video_prompt: "drama video prompt",
        transition_to_next: "cut",
      },
    ],
  };
}

function renderAt(path: string) {
  const { hook } = memoryLocation({ path });
  return render(
    <Router hook={hook}>
      <StudioCanvasRouter />
    </Router>,
  );
}

// 复刻真实入口的路由结构：父路由 `/:projectName` nest 出 `projectName` 参数，
// 供 StudioCanvasRouter 在 store 的 currentProjectName 还没同步时兜底判定演示态。
// 返回 `navigate` 以便测试原地切换路由——不卸载组件，复刻同一 StudioCanvasRouter 实例
// 从真实项目切到演示项目时的时序（wouter 路由参数变化不会重新挂载组件）。
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
    expect(screen.getByText("加载中...")).toBeInTheDocument();
  });

  it("keeps showing loading state on a deep link while the project detail request is still in flight", () => {
    // 首屏加载先落地 currentProjectName（数据置空）再等 refreshProject 结算（见
    // router.tsx）：currentProjectName 非空不代表详情已到达。直接打开 /characters 这类
    // 深链时,若只看 currentProjectName 会把空集合渲染成可交互的「空项目」页面。
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: null,
      projectDetailLoading: true,
    });

    renderAt("/characters");

    expect(screen.getByText("加载中...")).toBeInTheDocument();
    expect(screen.queryByTestId("character-card")).not.toBeInTheDocument();
  });

  it("routes characters/scenes/props/source/episodes views correctly", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: {
        "episode_1.json": makeScript(),
      },
    });

    const viewCharacters = renderAt("/characters");
    expect(screen.getByTestId("character-card")).toHaveAttribute("data-name", "Hero");
    viewCharacters.unmount();

    const viewScenes = renderAt("/scenes");
    expect(screen.getByTestId("scene-card")).toHaveAttribute("data-name", "Temple");
    viewScenes.unmount();

    const viewProps = renderAt("/props");
    expect(screen.getByTestId("prop-card")).toHaveAttribute("data-name", "Sword");
    viewProps.unmount();

    const viewSource = renderAt("/source/source%20file.txt");
    expect(screen.getByTestId("source-file-viewer")).toHaveTextContent("source file.txt");
    viewSource.unmount();

    const viewEpisodes = renderAt("/episodes/1");
    expect(screen.getByTestId("timeline-canvas")).toBeInTheDocument();
    expect(screen.getByTestId("timeline-has-script")).toHaveTextContent("yes");
    viewEpisodes.unmount();

    await waitFor(() => {
      expect(screen.queryByText("加载中...")).not.toBeInTheDocument();
    });
  });

  it("skips the provider/system-config lookups in the demo workbench", async () => {
    const providersSpy = vi.spyOn(API, "getProviders");
    const customProvidersSpy = vi.spyOn(API, "listCustomProviders");
    const systemConfigSpy = vi.spyOn(API, "getSystemConfig");

    useProjectsStore.setState({
      currentProjectName: DEMO_PROJECT_NAME,
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    renderAt("/episodes/1");

    await waitFor(() => {
      expect(screen.getByTestId("timeline-canvas")).toBeInTheDocument();
    });
    expect(providersSpy).not.toHaveBeenCalled();
    expect(customProvidersSpy).not.toHaveBeenCalled();
    expect(systemConfigSpy).not.toHaveBeenCalled();
  });

  // 上一条的正向对照：没有它，门控写成无条件 return 或整个 effect 被删都照样全绿
  it("still performs the provider/system-config lookups outside the demo workbench", async () => {
    const providersSpy = vi.spyOn(API, "getProviders").mockResolvedValue({ providers: [] });
    const customProvidersSpy = vi
      .spyOn(API, "listCustomProviders")
      .mockResolvedValue({ providers: [] });
    const systemConfigSpy = vi
      .spyOn(API, "getSystemConfig")
      .mockResolvedValue({ settings: {} } as Awaited<ReturnType<typeof API.getSystemConfig>>);

    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    renderAt("/episodes/1");

    await waitFor(() => {
      expect(providersSpy).toHaveBeenCalled();
    });
    expect(customProvidersSpy).toHaveBeenCalled();
    expect(systemConfigSpy).toHaveBeenCalled();
  });

  // 复刻真实入口时序：StudioWorkspace 要到自己的 effect 才把演示项目名写入 store，
  // 子组件的首轮渲染此时读到的 currentProjectName 仍是旧值/空值。只靠 store 判定
  // 会在首轮放行三个真实 GET；路由参数在渲染期即可用，必须兜底覆盖这段时间差。
  it("skips the provider/system-config lookups on first render before the store's currentProjectName catches up with the demo route", async () => {
    const providersSpy = vi.spyOn(API, "getProviders");
    const customProvidersSpy = vi.spyOn(API, "listCustomProviders");
    const systemConfigSpy = vi.spyOn(API, "getSystemConfig");

    useProjectsStore.setState({
      currentProjectName: null,
      currentProjectData: null,
      currentScripts: {},
    });

    renderAtProjectRoute(DEMO_PROJECT_NAME, "/episodes/1");

    await waitFor(() => {
      expect(screen.getByText("加载中...")).toBeInTheDocument();
    });
    expect(providersSpy).not.toHaveBeenCalled();
    expect(customProvidersSpy).not.toHaveBeenCalled();
    expect(systemConfigSpy).not.toHaveBeenCalled();
  });

  // 同一 StudioCanvasRouter 实例从真实项目切到演示项目（路由 nest 下 projectName 参数
  // 变化，组件不会重新挂载）时，上一个真实项目遗留的时长能力缓存必须清空，否则真实后端的
  // 时长限制会继续套用到演示的虚构时长上，重新触发「不兼容」误报。
  it("clears cached provider/backend duration capabilities when switching from a real project into the demo route", async () => {
    const providersSpy = vi.spyOn(API, "getProviders").mockResolvedValue({
      providers: [
        {
          id: "real-backend",
          display_name: "Real",
          description: "",
          status: "ready",
          media_types: ["video"],
          capabilities: [],
          configured_keys: [],
          missing_keys: [],
          models: {
            "model-1": {
              display_name: "Model 1",
              media_type: "video",
              capabilities: [],
              default: true,
              supported_durations: [5, 10],
              duration_resolution_constraints: {},
              resolutions: [],
              has_audio_track: false,
              voice_consistency: "none",
            },
          },
        },
      ],
    });
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "getSystemConfig").mockResolvedValue({
      settings: { default_video_backend: "real-backend/model-1" },
    } as Awaited<ReturnType<typeof API.getSystemConfig>>);

    useProjectsStore.setState({
      currentProjectName: "real-project",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    const view = renderAtProjectRoute("real-project", "/episodes/1");
    await waitFor(() => {
      expect(screen.getByTestId("timeline-duration-options")).toHaveTextContent("5,10");
    });

    providersSpy.mockClear();

    // 原地切换到演示项目：不卸载组件，复刻同一实例从真实项目切入演示态的时序，
    // 与 StudioWorkspace 的 effect 同步写入 store 一致。
    useProjectsStore.setState({
      currentProjectName: DEMO_PROJECT_NAME,
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });
    view.navigate(`/${DEMO_PROJECT_NAME}/episodes/1`);

    await waitFor(() => {
      // 缓存已清空：真实项目遗留的 providers/backend 不再参与时长兼容性比对
      expect(screen.getByTestId("timeline-duration-options")).toHaveTextContent("");
    });
    expect(providersSpy).not.toHaveBeenCalled();
  });

  // 逐镜头时长候选须按项目分辨率与生效 generation_mode 收窄——用户在设置里选了 1080p 却仍能把
  // 单个镜头改成 4 秒，入队时才被 backend 拒。反向用例守住「未受约束的分辨率下与改动前一致」：
  // 全集原样呈现，不因为接了收窄管线而误缩。
  it.each([
    ["1080p", "8"],
    ["720p", "4,6,8"],
  ])("narrows the per-shot duration options at %s", async (resolution, expected) => {
    const VEO = "gemini-aistudio/veo-3.1";
    vi.spyOn(API, "getProviders").mockResolvedValue({
      providers: [
        {
          id: "gemini-aistudio",
          display_name: "Gemini",
          description: "",
          status: "ready",
          media_types: ["video"],
          capabilities: [],
          configured_keys: [],
          missing_keys: [],
          models: {
            "veo-3.1": {
              display_name: "Veo 3.1",
              media_type: "video",
              capabilities: [],
              default: true,
              supported_durations: [4, 6, 8],
              duration_resolution_constraints: { "1080p": [8] },
              reference_image_durations: [8],
              resolutions: ["720p", "1080p"],
              has_audio_track: true,
              voice_consistency: "soft",
            },
          },
        },
      ],
    });
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "getSystemConfig").mockResolvedValue({
      settings: { default_video_backend: VEO },
    } as Awaited<ReturnType<typeof API.getSystemConfig>>);

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
      // 精确比对而非包含：全集 "4,6,8" 也含子串 "8"
      expect(screen.getByTestId("timeline-duration-options").textContent).toBe(expected);
    });
  });

  // 反方向：同一实例从演示项目切到真实项目时，store 的 currentProjectName 仍滞留上一轮的
  // 演示项目名（StudioWorkspace 的 effect 还没写入新值）。demoMode 若仍以 store 值兜底会误判
  // 为演示态，延迟真实项目的三个 GET；路由参数存在时须直接采信它。
  it("performs the provider/system-config lookups immediately after navigating from the demo route to a real project, before the store catches up", async () => {
    const providersSpy = vi.spyOn(API, "getProviders").mockResolvedValue({ providers: [] });
    const customProvidersSpy = vi
      .spyOn(API, "listCustomProviders")
      .mockResolvedValue({ providers: [] });
    const systemConfigSpy = vi
      .spyOn(API, "getSystemConfig")
      .mockResolvedValue({ settings: {} } as Awaited<ReturnType<typeof API.getSystemConfig>>);

    useProjectsStore.setState({
      currentProjectName: DEMO_PROJECT_NAME,
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    const view = renderAtProjectRoute(DEMO_PROJECT_NAME, "/episodes/1");
    await waitFor(() => {
      expect(screen.getByTestId("timeline-canvas")).toBeInTheDocument();
    });
    expect(providersSpy).not.toHaveBeenCalled();

    // 原地切换到真实项目：只挪路由，store 的 currentProjectName 刻意留在演示项目名，
    // 复刻 StudioWorkspace 的 effect 尚未执行完成的时间窗。
    view.navigate("/real-project/episodes/1");

    await waitFor(() => {
      expect(providersSpy).toHaveBeenCalled();
    });
    expect(customProvidersSpy).toHaveBeenCalled();
    expect(systemConfigSpy).toHaveBeenCalled();
  });

  // demo→真实项目切换后路由已让 demoMode 变为 false，但 store 的 currentProjectName 还滞留
  // 演示项目名；只看 demoMode 会对着后端不存在的演示项目发一次必然失败的 /video-capabilities。
  it("skips the video-capabilities lookup for the stale demo project name after navigating to a real project, before the store catches up", async () => {
    const capabilitiesSpy = vi
      .spyOn(API, "getVideoCapabilities")
      .mockResolvedValue({ supported_durations: [5, 10] } as Awaited<
        ReturnType<typeof API.getVideoCapabilities>
      >);
    vi.spyOn(API, "getProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "getSystemConfig").mockResolvedValue({
      settings: {},
    } as Awaited<ReturnType<typeof API.getSystemConfig>>);

    useProjectsStore.setState({
      currentProjectName: DEMO_PROJECT_NAME,
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    const view = renderAtProjectRoute(DEMO_PROJECT_NAME, "/episodes/1");
    await waitFor(() => {
      expect(screen.getByTestId("timeline-canvas")).toBeInTheDocument();
    });

    // 原地切换到真实项目：只挪路由，store 的 currentProjectName 刻意留在演示项目名，
    // 复刻 StudioWorkspace 的 effect 尚未执行完成的时间窗。
    view.navigate("/real-project/episodes/1");

    await waitFor(() => {
      expect(API.getProviders).toHaveBeenCalled();
    });
    expect(capabilitiesSpy).not.toHaveBeenCalledWith(DEMO_PROJECT_NAME);
  });

  it("shows EpisodeSourceReview instead of TimelineCanvas when an episode has no script and no draft", () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
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

  it("runs character callbacks and reports API failures with toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "updateCharacter").mockResolvedValue({ success: true });
    vi.spyOn(API, "uploadFile").mockResolvedValue({ success: true, path: "x", url: "y" });
    vi.spyOn(API, "generateCharacter").mockResolvedValue({ success: true, task_id: "t-1", deduped: false, message: "已提交" });
    vi.spyOn(API, "addCharacter").mockResolvedValue({ success: true });

    renderAt("/characters");

    fireEvent.click(screen.getByText("update-character"));
    await waitFor(() => {
      expect(API.updateCharacter).toHaveBeenCalledWith("demo", "Hero", {
        description: "new desc",
        voice_style: "new voice",
      });
      expect(API.uploadFile).toHaveBeenNthCalledWith(
        1,
        "demo",
        "character_ref",
        expect.any(File),
        "Hero",
      );
      expect(API.getProject).toHaveBeenCalled();
    });

    fireEvent.click(screen.getByText("generate-character"));
    await waitFor(() => {
      expect(API.generateCharacter).toHaveBeenCalledWith(
        "demo",
        "Hero",
        "hero description",
      );
      expect(useAppStore.getState().toast?.text).toContain("生成任务已提交");
      expect(useAppStore.getState().toast?.tone).toBe("success");
      // 入队成功后应立即乐观占用该角色，避免 SSE 轮询落地前的空窗被误判为空闲
      // 而并发触发 image_edit（见 tasks-store.ts 乐观占用小节）。
      const { tasks, optimisticActive } = useTasksStore.getState();
      expect(selectActiveResourceIds(tasks, "character", "demo", optimisticActive).has("Hero")).toBe(true);
    });

    // Test add character flow: click "add" button is not directly accessible in CharacterCard mock;
    // instead, we test the AddCharacterForm path by navigating with the form already showing.
    // The add-character button is on CharactersPage which is not directly exposed; we test the form submit instead.
  });

  it("refreshes the project even when the audio upload step fails partway through save", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "updateCharacter").mockResolvedValue({ success: true });
    vi.spyOn(API, "uploadFile").mockRejectedValue(new Error("audio_duration_out_of_range"));

    renderAt("/characters");

    fireEvent.click(screen.getByText("update-character-with-audio"));
    await waitFor(() => {
      expect(API.uploadFile).toHaveBeenCalledWith(
        "demo",
        "character_audio_ref",
        expect.any(File),
        expect.any(String),
      );
      // description/voice_style 已持久化成功，仅音频上传失败：即便整体 catch 到错误，
      // 也要刷新 store 让已保存的部分反映到 UI，而不是让用户误以为整个保存都没生效
      expect(API.getProject).toHaveBeenCalled();
      expect(useAppStore.getState().toast?.text).toContain("更新角色失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });
  });

  it("runs scene callbacks and reports API failures with toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "updateProjectScene").mockRejectedValue(new Error("scene update failed"));
    vi.spyOn(API, "generateProjectScene").mockRejectedValue(new Error("scene generate failed"));

    renderAt("/scenes");

    fireEvent.click(screen.getByText("update-scene"));
    await waitFor(() => {
      expect(API.updateProjectScene).toHaveBeenCalledWith("demo", "Temple", {
        description: "new scene desc",
      });
      expect(useAppStore.getState().toast?.text).toContain("更新场景失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });

    fireEvent.click(screen.getByText("generate-scene"));
    await waitFor(() => {
      expect(API.generateProjectScene).toHaveBeenCalledWith("demo", "Temple", "ancient temple");
      expect(useAppStore.getState().toast?.text).toContain("提交失败");
    });
  });

  it("runs prop callbacks and reports API failures with toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "updateProjectProp").mockRejectedValue(new Error("prop update failed"));
    vi.spyOn(API, "generateProjectProp").mockRejectedValue(new Error("prop generate failed"));

    renderAt("/props");

    fireEvent.click(screen.getByText("update-prop"));
    await waitFor(() => {
      expect(API.updateProjectProp).toHaveBeenCalledWith("demo", "Sword", {
        description: "new prop desc",
      });
      expect(useAppStore.getState().toast?.text).toContain("更新道具失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });

    fireEvent.click(screen.getByText("generate-prop"));
    await waitFor(() => {
      expect(API.generateProjectProp).toHaveBeenCalledWith("demo", "Sword", "rusty sword");
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
      expect(API.generateProjectScene).toHaveBeenCalledWith("demo", "Temple", "ancient temple");
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
      expect(API.generateProjectProp).toHaveBeenCalledWith("demo", "Sword", "rusty sword");
      const { tasks, optimisticActive } = useTasksStore.getState();
      expect(selectActiveResourceIds(tasks, "prop", "demo", optimisticActive).has("Sword")).toBe(true);
    });
  });

  it("runs product callbacks and reports API failures with toast", async () => {
    const projectData = makeProjectData({
      products: { Phone: { description: "sleek phone" } },
    });
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: projectData,
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: projectData,
      scripts: { "episode_1.json": makeScript() },
    });
    const updateSpy = vi.spyOn(API, "updateProjectProduct").mockResolvedValue({ success: true });
    const generateSpy = vi
      .spyOn(API, "generateProjectProduct")
      .mockResolvedValue({ success: true, task_id: "t-1", deduped: false, message: "已提交" });
    const addSpy = vi.spyOn(API, "addProjectProduct").mockResolvedValue({ success: true });

    renderAt("/products");
    expect(screen.getByTestId("products-page")).toHaveAttribute("data-names", "Phone");

    fireEvent.click(screen.getByText("update-product"));
    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith("demo", "Phone", {
        description: "new product desc",
      });
      expect(API.getProject).toHaveBeenCalled();
    });

    fireEvent.click(screen.getByText("generate-product"));
    await waitFor(() => {
      expect(generateSpy).toHaveBeenCalledWith("demo", "Phone", "sleek phone");
      expect(useAppStore.getState().toast?.text).toContain("标准参考图生成任务已提交");
      expect(useAppStore.getState().toast?.tone).toBe("success");
      const { tasks, optimisticActive } = useTasksStore.getState();
      expect(selectActiveResourceIds(tasks, "product", "demo", optimisticActive).has("Phone")).toBe(true);
    });

    fireEvent.click(screen.getByText("add-product"));
    await waitFor(() => {
      expect(addSpy).toHaveBeenCalledWith("demo", "NewPhone", "desc", "Acme");
      expect(useAppStore.getState().toast?.text).toContain("已添加");
    });

    fireEvent.click(screen.getByText("add-product-no-brand"));
    await waitFor(() => {
      expect(addSpy).toHaveBeenCalledWith("demo", "NewPhone", "desc", undefined);
    });
  });

  it("reports product callback failures with error toasts", async () => {
    const projectData = makeProjectData({
      products: { Phone: { description: "sleek phone" } },
    });
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: projectData,
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: projectData,
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "updateProjectProduct").mockRejectedValue(new Error("product update failed"));
    vi.spyOn(API, "generateProjectProduct").mockRejectedValue(new Error("product generate failed"));
    vi.spyOn(API, "addProjectProduct").mockRejectedValue(new Error("product add failed"));

    renderAt("/products");

    fireEvent.click(screen.getByText("update-product"));
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toContain("更新产品失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });

    fireEvent.click(screen.getByText("generate-product"));
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toContain("提交失败");
    });

    fireEvent.click(screen.getByText("add-product"));
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toContain("添加失败");
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
      expect(useAppStore.getState().toast?.text).toContain("更新 Prompt 失败");
    });

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

  // TimelineCanvas 的 ShotDetail.handleSave / handleRefsSave 靠 await 这个回调维持保存中
  // 状态；handleUpdatePrompt 改回真实结果契约后若在此处包一层 voidPromise（丢弃返回值的
  // 同时也让包装函数立即 resolve），await 会在 PATCH 真正落库前就提前结束。
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

    // 放几轮微任务/宏任务过去：底层 PATCH 仍未落地，回调不能提前 resolve——
    // 若用 voidPromise 包一层，包装函数会在这里已经 resolve，暴露不出真正的 bug。
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
      // duration 取镜头自身 duration_seconds(5),不回退默认值 4
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

  // PATCH 成功但本地刷新失败/取消时不能报告成功：调用方（AdReferenceVideoCanvas 的
  // 镜头编辑）会据此清空本地草稿，届时 store 里仍是旧剧本，回显会与用户刚提交的值不符。
  // 与 handleMoveShot 的既有契约（"moves an ad shot..." 用例）保持一致。
  it("reports the shot PATCH as failed when the local refresh doesn't land", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({
        content_mode: "ad",
        generation_mode: "reference_video",
      }),
      currentScripts: { "episode_1.json": makeAdScript() },
    });

    vi.spyOn(API, "updateShot").mockResolvedValue({ success: true });
    vi.spyOn(API, "getProject").mockRejectedValue(new Error("network down"));

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("ad-reference-update-prompt"));
    await waitFor(() => {
      expect(screen.getByText("ad-reference-update-prompt")).toHaveAttribute(
        "data-update-result",
        "false",
      );
    });
  });

  it("moves an ad shot by submitting the full reordered id list", async () => {
    const script = makeAdScript() as AdEpisodeScript;
    script.shots.push({
      shot_id: "SEG-2",
      section: "cta",
      duration_seconds: 3,
      voiceover_text: "立即下单",
      image_prompt: "p2",
      video_prompt: "v2",
      transition_to_next: "cut",
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
    const reorderSpy = vi.spyOn(API, "reorderShots").mockResolvedValue({ success: true });

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("move-shot-later"));
    await waitFor(() => {
      expect(reorderSpy).toHaveBeenCalledWith("demo", "episode_1.json", ["SEG-2", "SEG-1"]);
    });
    // 重排 + 本地刷新都成功 → 报告移动成功
    await waitFor(() => {
      expect(screen.getByText("move-shot-later")).toHaveAttribute("data-move-result", "true");
    });
  });

  it("reports move failure and toasts when the reorder request fails", async () => {
    const script = makeAdScript() as AdEpisodeScript;
    script.shots.push({
      shot_id: "SEG-2",
      section: "cta",
      duration_seconds: 3,
      voiceover_text: "立即下单",
      image_prompt: "p2",
      video_prompt: "v2",
      transition_to_next: "cut",
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
    vi.spyOn(API, "reorderShots").mockRejectedValue(new Error("server boom"));

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("move-shot-later"));
    await waitFor(() => {
      expect(screen.getByText("move-shot-later")).toHaveAttribute("data-move-result", "false");
    });
    expect(useAppStore.getState().toast?.text).toContain("server boom");
    expect(useAppStore.getState().toast?.tone).toBe("error");
  });

  it("reports move failure when local refresh fails after a successful reorder", async () => {
    const script = makeAdScript() as AdEpisodeScript;
    script.shots.push({
      shot_id: "SEG-2",
      section: "cta",
      duration_seconds: 3,
      voiceover_text: "立即下单",
      image_prompt: "p2",
      video_prompt: "v2",
      transition_to_next: "cut",
    });
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ content_mode: "ad" }),
      currentScripts: { "episode_1.json": script },
    });

    // 重排接口成功，但项目刷新失败：本地 segments 仍是旧顺序，
    // 必须报告失败，否则调用方会推进 selectedIndex 切到错误镜头
    vi.spyOn(API, "getProject").mockRejectedValue(new Error("network down"));
    vi.spyOn(API, "reorderShots").mockResolvedValue({ success: true });

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("move-shot-later"));
    await waitFor(() => {
      expect(screen.getByText("move-shot-later")).toHaveAttribute("data-move-result", "false");
    });
  });

  it("routes ad + reference_video projects to the derived-group canvas with the script's shots", async () => {
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

    const canvas = screen.getByTestId("ad-reference-canvas");
    expect(canvas).toHaveAttribute("data-has-script", "yes");
    expect(canvas).toHaveTextContent("SEG-1");
    // 分镜编辑画布在该路径下不再渲染
    expect(screen.queryByTestId("timeline-canvas")).not.toBeInTheDocument();
    // script_file 存在 → 标题可编辑入口透传为 true
    expect(screen.getByTestId("ad-reference-can-edit-title")).toHaveTextContent("yes");

    fireEvent.click(screen.getByText("ad-reference-save-title"));
    await waitFor(() => {
      expect(API.updateEpisode).toHaveBeenCalledWith("demo", 1, { title: "新标题" });
    });
  });

  // 演示项目当前的 content_mode 恒为 narration，不会真的落到这条路由分支；本用例直接摆出
  // demoMode + ad + reference_video 的组合，核对调用点本身的门控独立于「当前是否可达」——
  // 与其余画布一致，demoMode 下不得把写入回调暴露给子组件。
  it("does not expose the edit callback to the derived-group canvas in demo mode", () => {
    useProjectsStore.setState({
      currentProjectName: DEMO_PROJECT_NAME,
      currentProjectData: makeProjectData({
        content_mode: "ad",
        generation_mode: "reference_video",
      }),
      currentScripts: { "episode_1.json": makeAdScript() },
    });

    renderAt("/episodes/1");

    expect(screen.getByTestId("ad-reference-canvas")).toHaveAttribute("data-editable", "no");
  });

  it("falls back to an empty shot list when the episode script isn't an ad script", () => {
    // 路由分支只按 project.content_mode 判定 isAd；剧本条目理应与项目模式一致，
    // 但类型上是各自独立的 union，ternary 的 : [] 分支正是应对二者暂时不一致的防御。
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

    const canvas = screen.getByTestId("ad-reference-canvas");
    expect(canvas).toHaveAttribute("data-has-script", "yes");
    expect(canvas).not.toHaveTextContent("SEG-1");
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
    expect(screen.queryByTestId("ad-reference-canvas")).not.toBeInTheDocument();
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
      expect(API.generateCharacter).toHaveBeenCalledWith("demo", "Hero", "hero description");
      expect(useAppStore.getState().toast?.text).toContain("提交失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });
  });

  it("saves the episode title and shows a success toast", async () => {
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

    // script_file 存在 → 标题可编辑入口透传为 true
    expect(screen.getByTestId("timeline-can-edit-title")).toHaveTextContent("yes");

    fireEvent.click(screen.getByText("save-title"));
    await waitFor(() => {
      expect(API.updateEpisode).toHaveBeenCalledWith("demo", 1, { title: "新标题" });
      expect(API.getProject).toHaveBeenCalled();
      expect(useAppStore.getState().toast?.text).toContain("分集标题已更新");
      expect(useAppStore.getState().toast?.tone).toBe("success");
    });
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

    fireEvent.click(screen.getByText("save-title"));
    await waitFor(() => {
      expect(API.updateEpisode).toHaveBeenCalledWith("demo", 1, { title: "新标题" });
      expect(useAppStore.getState().toast?.text).toContain("更新分集标题失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });
  });

  it("submits narration generation and shows a success toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
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
      expect(useAppStore.getState().toast?.text).toContain("旁白");
      expect(useAppStore.getState().toast?.tone).toBe("success");
    });
  });

  it("reports narration generation failure with an error toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateNarrationAudio").mockRejectedValue(new Error("tts failed"));

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("generate-narration"));
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toContain("生成旁白失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });
  });

  it("submits episode narration batch and reports the submitted count", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateEpisodeNarrationAudio").mockResolvedValue({
      success: true,
      task_ids: ["t-1", "t-2"],
      deduped: false,
      message: "已提交",
    });

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("generate-episode-narration"));
    await waitFor(() => {
      expect(API.generateEpisodeNarrationAudio).toHaveBeenCalledWith("demo", "episode_1.json");
      expect(useAppStore.getState().toast?.text).toContain("2");
      expect(useAppStore.getState().toast?.tone).toBe("success");
    });
  });

  it("tells the user when episode narration has nothing missing", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateEpisodeNarrationAudio").mockResolvedValue({
      success: true,
      task_ids: [],
      deduped: false,
      message: "无需补缺",
    });

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("generate-episode-narration"));
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toContain("所有分镜均已生成旁白");
      expect(useAppStore.getState().toast?.tone).toBe("success");
    });
  });

  it("blocks narration generation when no audio provider is configured", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });
    useConfigStatusStore.setState({
      initialized: true,
      availableMediaTypes: ["image", "video", "text"],
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
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
      expect(useAppStore.getState().toast?.text).toContain("宫格生成失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });
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
      deduped: false,
      message: "已提交 0 个宫格生成任务",
    });

    renderAt("/episodes/1");

    fireEvent.click(await screen.findByText("generate-grid"));
    await waitFor(() => {
      expect(API.generateGrid).toHaveBeenCalledWith("demo", 1, "episode_1.json", undefined);
      expect(useAppStore.getState().toast?.text).toBe("已提交 0 个宫格生成任务");
    });

    const { tasks, optimisticActiveScriptFile } = useTasksStore.getState();
    expect(
      selectHasActiveTaskForScriptFile(tasks, "grid", "episode_1.json", "demo", optimisticActiveScriptFile),
    ).toBe(false);
  });
});
