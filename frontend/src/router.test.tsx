import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useAuthStore } from "@/stores/auth-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useProjectsStore } from "@/stores/projects-store";
import { createDeferred } from "@/test/deferred";
import { makeWorkspaceResponse } from "@/test/factories";
import { AppRoutes } from "@/router";

vi.mock("@/components/layout", () => ({
  StudioLayout: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="studio-layout">{children}</div>
  ),
}));

vi.mock("@/components/canvas/StudioCanvasRouter", () => ({
  StudioCanvasRouter: () => <div data-testid="studio-canvas-router">Studio Canvas</div>,
}));

vi.mock("@/components/pages/ProjectsPage", () => ({
  ProjectsPage: () => <div data-testid="projects-page">Projects Page</div>,
}));

vi.mock("@/components/pages/SystemConfigPage", () => ({
  SystemConfigPage: () => <div data-testid="system-config-page">System Config Page</div>,
}));

vi.mock("@/components/pages/ProjectSettingsPage", () => ({
  ProjectSettingsPage: () => <div data-testid="project-settings-page">Project Settings Page</div>,
}));

function renderAt(path: string) {
  const { hook } = memoryLocation({ path });
  return render(
    <Router hook={hook}>
      <AppRoutes />
    </Router>,
  );
}

function resetStores(): void {
  useAppStore.setState(useAppStore.getInitialState(), true);
  useProjectsStore.setState(useProjectsStore.getInitialState(), true);
  useAssistantStore.setState(useAssistantStore.getInitialState(), true);
}

describe("AppRoutes", () => {
  beforeEach(() => {
    resetStores();
    useAuthStore.setState({ isAuthenticated: true, isLoading: false });
    useConfigStatusStore.setState({ initialized: true });
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("redirects root path to /app/projects", async () => {
    renderAt("/");
    expect(await screen.findByTestId("projects-page")).toBeInTheDocument();
  });

  it("redirects /app to /app/projects", async () => {
    renderAt("/app");
    expect(await screen.findByTestId("projects-page")).toBeInTheDocument();
  });

  it("renders 404 for unknown routes", () => {
    renderAt("/not-found");
    expect(screen.getByRole("heading", { name: "页面未找到" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回首页" })).toHaveAttribute("href", "/app/projects");
  });

  it.each(["/app/projects/demo/lorebook", "/app/projects/demo/clues", "/app/projects/demo/unknown/"])(
    "keeps the workspace shell for the unregistered workspace path %s",
    async (path) => {
      vi.spyOn(API, "getWorkspace").mockResolvedValue(
        makeWorkspaceResponse({ title: "Demo", content_mode: "narration", style: "Anime", episodes: [], characters: {}, scenes: {}, props: {} }),
      );
      renderAt(path);
      expect(await screen.findByTestId("studio-canvas-router")).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "页面未找到" })).not.toBeInTheDocument();
    },
  );

  it("loads project workspace and resets assistant state without fetching scripts", async () => {
    const getScriptSpy = vi.spyOn(API, "getScript");
    const getProjectSpy = vi.spyOn(API, "getProject");
    vi.spyOn(API, "getWorkspace").mockResolvedValue(
      makeWorkspaceResponse({
        title: "Demo Project",
        content_mode: "narration",
        style: "Anime",
        episodes: [{ episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "generated" }],
        characters: {},
        scenes: {},
        props: {},
      }),
    );

    useAssistantStore.setState({
      sessions: [
        {
          id: "session-1",
          project_name: "old",
          title: "Old",
          status: "idle",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
      currentSessionId: "session-1",
      turns: [{ type: "user", content: [{ type: "text", text: "hello" }] }],
      draftTurn: { type: "assistant", content: [{ type: "text", text: "draft" }] },
      sessionStatus: "running",
      isDraftSession: true,
    });

    const view = renderAt("/app/projects/demo");

    expect(await screen.findByTestId("studio-layout")).toBeInTheDocument();
    expect(await screen.findByTestId("studio-canvas-router")).toBeInTheDocument();
    await waitFor(() => {
      expect(API.getWorkspace).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
    });
    expect(getProjectSpy).not.toHaveBeenCalled();
    expect(getScriptSpy).not.toHaveBeenCalled();

    const assistant = useAssistantStore.getState();
    expect(assistant.sessions).toEqual([]);
    expect(assistant.currentSessionId).toBeNull();
    expect(assistant.turns).toEqual([]);
    expect(assistant.draftTurn).toBeNull();
    expect(assistant.sessionStatus).toBeNull();
    expect(assistant.isDraftSession).toBe(false);

    await waitFor(() => {
      const projectState = useProjectsStore.getState();
      expect(projectState.currentProjectName).toBe("demo");
      expect(projectState.currentProjectData?.title).toBe("Demo Project");
      expect(projectState.projectDetailLoading).toBe(false);
    });

    view.unmount();
    expect(useProjectsStore.getState().currentProjectName).toBeNull();
    expect(useProjectsStore.getState().currentProjectData).toBeNull();
  });

  it("直达单集页时并行拉取 workspace 与当前集剧本，快速切集丢弃过期响应", async () => {
    const ws = makeWorkspaceResponse({
      title: "Demo",
      content_mode: "narration",
      style: "Anime",
      episodes: [
        { episode: 1, title: "EP1", script_file: "scripts/episode_1.json", script_status: "generated" },
        { episode: 2, title: "EP2", script_file: "scripts/episode_2.json", script_status: "generated" },
      ],
      characters: {},
      scenes: {},
      props: {},
    });
    const workspace = createDeferred<typeof ws>();
    vi.spyOn(API, "getWorkspace").mockReturnValue(workspace.promise);
    const ep1Deferred = createDeferred<Awaited<ReturnType<typeof API.getScript>>>();
    const ep2Deferred = createDeferred<Awaited<ReturnType<typeof API.getScript>>>();
    let ep1Signal: AbortSignal | undefined;
    const getScriptSpy = vi.spyOn(API, "getScript").mockImplementation((_proj, file, options) => {
      if (file === "episode_1.json") {
        ep1Signal = options?.signal;
        return ep1Deferred.promise;
      }
      return ep2Deferred.promise;
    });

    const { hook, navigate } = memoryLocation({ path: "/app/projects/demo/episodes/1" });
    render(
      <Router hook={hook}>
        <AppRoutes />
      </Router>,
    );

    await waitFor(() => {
      expect(API.getWorkspace).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
      expect(getScriptSpy).toHaveBeenCalledWith("demo", "episode_1.json", { speculative: true, onVersion: expect.any(Function), signal: expect.any(AbortSignal) });
    });

    await act(async () => workspace.resolve(ws));
    act(() => navigate("/app/projects/demo/episodes/2"));
    await waitFor(() => {
      expect(getScriptSpy).toHaveBeenCalledWith("demo", "episode_2.json", { signal: expect.any(AbortSignal) });
    });
    expect(ep1Signal?.aborted).toBe(true);

    await act(async () => {
      ep2Deferred.resolve({ script: { episode: 2, segments: [] }, revision: "r2" } as never);
    });

    await waitFor(() => {
      expect(Object.keys(useProjectsStore.getState().currentScripts)).toEqual(["episode_2.json"]);
      expect(useProjectsStore.getState().activeEpisodeScript.episode).toBe(2);
    });
    await act(async () => ep1Deferred.resolve({ script: { episode: 1, segments: [] }, revision: "r1" } as never));
    expect(Object.keys(useProjectsStore.getState().currentScripts)).toEqual(["episode_2.json"]);
    expect(useProjectsStore.getState().activeEpisodeScript.episode).toBe(2);
  });

  it("自定义绑定的剧本读取期间切到同项目概览会结束工作台加载", async () => {
    vi.spyOn(API, "getWorkspace").mockResolvedValue(makeWorkspaceResponse({
      title: "Demo", content_mode: "narration", style: "", characters: {},
      episodes: [{ episode: 1, title: "EP1", script_file: "scripts/custom.json", script_status: "generated" }],
    }));
    const read = createDeferred<Awaited<ReturnType<typeof API.getScript>>>();
    const script = vi.spyOn(API, "getScript").mockReturnValue(read.promise);
    const { hook, navigate } = memoryLocation({ path: "/app/projects/demo/episodes/1" });
    render(<Router hook={hook}><AppRoutes /></Router>);
    await waitFor(() => expect(script).toHaveBeenCalledWith("demo", "custom.json", { signal: expect.any(AbortSignal) }));
    act(() => navigate("/app/projects/demo/overview"));
    await act(async () => read.resolve({ script: { episode: 1, segments: [] } as never, revision: "r1" }));
    await waitFor(() => expect(useProjectsStore.getState().projectDetailLoading).toBe(false));
    expect(useProjectsStore.getState().currentScripts).toEqual({});
  });

  it("切换界面语言不重跑真实项目的加载", async () => {
    vi.spyOn(API, "getWorkspace").mockResolvedValue(
      makeWorkspaceResponse({
        title: "Demo Project",
        content_mode: "narration",
        style: "",
        episodes: [],
        characters: {},
      }),
    );

    renderAt("/app/projects/demo");
    await waitFor(() => {
      expect(useProjectsStore.getState().currentProjectData?.title).toBe("Demo Project");
    });

    await act(async () => {
      await i18n.changeLanguage("en");
    });

    expect(API.getWorkspace).toHaveBeenCalledTimes(1);
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("Demo Project");

    await act(async () => {
      await i18n.changeLanguage("zh");
    });
  });

  it("演示项目不发请求，数据随界面语言重灌", async () => {
    vi.spyOn(API, "getWorkspace").mockResolvedValue(
      makeWorkspaceResponse({ title: "", content_mode: "narration", style: "", episodes: [], characters: {} }),
    );

    const view = renderAt(`/app/projects/${DEMO_PROJECT_NAME}`);
    await waitFor(() => {
      expect(useProjectsStore.getState().currentProjectName).toBe(DEMO_PROJECT_NAME);
      expect(useProjectsStore.getState().currentProjectData).not.toBeNull();
    });
    expect(API.getWorkspace).not.toHaveBeenCalled();

    const zhTitle = useProjectsStore.getState().currentProjectData?.title;
    await act(async () => {
      await i18n.changeLanguage("en");
    });
    const enTitle = useProjectsStore.getState().currentProjectData?.title;
    expect(enTitle).not.toBe(zhTitle);

    await act(async () => {
      await i18n.changeLanguage("zh");
    });
    view.unmount();
    expect(useProjectsStore.getState().currentProjectName).toBeNull();
  });

  it("离开项目会中止在途的详情请求", async () => {
    let capturedSignal: AbortSignal | undefined;
    vi.spyOn(API, "getWorkspace").mockImplementation(
      (_name, options) =>
        new Promise((_resolve, reject) => {
          capturedSignal = options?.signal;
          // 模拟真实 fetch + AbortSignal 语义：abort 后 reject，而不是永久悬挂——
          // 否则 refreshProject 的共享「在途」标志会被这条请求永久卡住，污染同一
          // 文件里排在后面的用例（它们的 refreshProject 调用会被无限期排队）。
          options?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
    );

    const view = renderAt("/app/projects/demo");
    await waitFor(() => expect(capturedSignal).toBeDefined());
    expect(capturedSignal?.aborted).toBe(false);

    view.unmount();
    expect(capturedSignal?.aborted).toBe(true);
  });

  it("快速切换项目：旧项目的迟到响应不覆盖新项目的首屏数据", async () => {
    let resolveA!: (value: Awaited<ReturnType<typeof API.getWorkspace>>) => void;
    const pendingA = new Promise<Awaited<ReturnType<typeof API.getWorkspace>>>((res) => {
      resolveA = res;
    });
    vi.spyOn(API, "getWorkspace").mockImplementation((name) =>
      name === "A"
        ? pendingA
        : Promise.resolve(
            makeWorkspaceResponse({ title: "B-数据", content_mode: "narration", style: "", episodes: [], characters: {} }),
          ),
    );

    const seenTitles: Array<string | undefined> = [];
    const unsubscribe = useProjectsStore.subscribe((s) =>
      seenTitles.push(s.currentProjectData?.title),
    );

    const { hook, navigate } = memoryLocation({ path: "/app/projects/A" });
    render(
      <Router hook={hook}>
        <AppRoutes />
      </Router>,
    );
    await waitFor(() => {
      expect(API.getWorkspace).toHaveBeenCalledWith("A", { signal: expect.any(AbortSignal) });
    });

    act(() => navigate("/app/projects/B"));
    await act(async () => {
      resolveA(
        makeWorkspaceResponse({ title: "A-迟到数据", content_mode: "narration", style: "", episodes: [], characters: {} }),
      );
      await pendingA;
    });

    await waitFor(() => {
      const s = useProjectsStore.getState();
      expect(s.currentProjectName).toBe("B");
      expect(s.currentProjectData?.title).toBe("B-数据");
      expect(s.projectDetailLoading).toBe(false);
    });
    unsubscribe();
    expect(seenTitles).not.toContain("A-迟到数据");
  });

  it("keeps project name when loading project details fails", async () => {
    vi.spyOn(API, "getWorkspace").mockRejectedValue(new Error("network"));

    renderAt("/app/projects/fail-demo");

    expect(await screen.findByTestId("studio-layout")).toBeInTheDocument();
    await waitFor(() => {
      const projectState = useProjectsStore.getState();
      expect(projectState.currentProjectName).toBe("fail-demo");
      expect(projectState.currentProjectData).toBeNull();
      expect(projectState.projectDetailLoading).toBe(false);
    });
  });

  it("redirects unauthenticated nested project URL to top-level /login", async () => {
    useAuthStore.setState({ isAuthenticated: false, isLoading: false });
    renderAt("/app/projects/demo");
    expect(await screen.findByTestId("login-page")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "页面未找到" })).not.toBeInTheDocument();
  });

  it("redirects the demo project's settings deep link to global settings", async () => {
    renderAt(`/app/projects/${DEMO_PROJECT_NAME}/settings`);
    expect(await screen.findByTestId("system-config-page")).toBeInTheDocument();
    expect(screen.queryByTestId("project-settings-page")).not.toBeInTheDocument();
  });

  it("still renders project settings for a real project", async () => {
    renderAt("/app/projects/demo/settings");
    expect(await screen.findByTestId("project-settings-page")).toBeInTheDocument();
  });

  it("redirects unauthenticated non-nested protected route to /login", async () => {
    useAuthStore.setState({ isAuthenticated: false, isLoading: false });
    renderAt("/app/projects");
    expect(await screen.findByTestId("login-page")).toBeInTheDocument();
  });

  it("ConfigStatusLoader 挂载后拉取配置状态,未初始化时按退避重试", async () => {
    vi.useFakeTimers();
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    vi.spyOn(API, "getProviders").mockRejectedValue(new Error("backend not ready"));
    vi.spyOn(API, "listCustomProviders").mockRejectedValue(new Error("backend not ready"));
    vi.spyOn(API, "getSystemConfig").mockRejectedValue(new Error("backend not ready"));

    renderAt("/app/projects");

    await vi.advanceTimersByTimeAsync(0);
    expect(API.getProviders).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(800);
    expect(API.getProviders).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(1600);
    expect(API.getProviders).toHaveBeenCalledTimes(3);
  });
});
