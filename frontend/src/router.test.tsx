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
    // ConfigStatusLoader always mounts in AppRoutes; initialized prestate short-circuits fetch(),
    // leaving configuration states outside router tests and avoiding unmocked provider APIs/backoff.
    useConfigStatusStore.setState({ initialized: true });
    vi.restoreAllMocks();
  });

  afterEach(() => {
    // Some cases use fake timers; restore consistently to avoid cross-case contamination.
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

  // Removed lorebook/clues entries have no redirects; unregistered project subpaths retain workspace shells with canvas empty states.
  it.each(["/app/projects/demo/lorebook", "/app/projects/demo/clues", "/app/projects/demo/unknown/"])(
    "keeps the workspace shell for the unregistered workspace path %s",
    async (path) => {
      vi.spyOn(API, "getProject").mockResolvedValue({
        project: { title: "Demo", content_mode: "narration", style: "Anime", episodes: [], characters: {}, scenes: {}, props: {} },
        scripts: {},
      });
      renderAt(path);
      expect(await screen.findByTestId("studio-canvas-router")).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "页面未找到" })).not.toBeInTheDocument();
    },
  );

  it("loads project workspace and resets assistant state", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo Project",
        content_mode: "narration",
        style: "Anime",
        episodes: [],
        characters: {},
        scenes: {},
        props: {},
      },
      scripts: {},
    });

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
      expect(API.getProject).toHaveBeenCalledWith("demo", { signal: expect.any(AbortSignal) });
    });

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

  it("切换界面语言不重跑真实项目的加载", async () => {
    // Regression: demo-project constants refresh with `t`, but must not enter real-project loading-effect dependencies;
    // otherwise language changes clear stores/flash empty states and erase Agent sessions.
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo Project",
        content_mode: "narration",
      } as never,
      scripts: {},
    });

    renderAt("/app/projects/demo");
    await waitFor(() => {
      expect(useProjectsStore.getState().currentProjectData?.title).toBe("Demo Project");
    });

    await act(async () => {
      await i18n.changeLanguage("en");
    });

    expect(API.getProject).toHaveBeenCalledTimes(1);
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("Demo Project");

    await act(async () => {
      await i18n.changeLanguage("zh");
    });
  });

  it("演示项目不发请求，数据随界面语言重灌", async () => {
    vi.spyOn(API, "getProject").mockResolvedValue({ project: {} as never, scripts: {} });

    const view = renderAt(`/app/projects/${DEMO_PROJECT_NAME}`);
    await waitFor(() => {
      expect(useProjectsStore.getState().currentProjectName).toBe(DEMO_PROJECT_NAME);
      expect(useProjectsStore.getState().currentProjectData).not.toBeNull();
    });
    expect(API.getProject).not.toHaveBeenCalled();

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
    vi.spyOn(API, "getProject").mockImplementation(
      (_name, options) =>
        new Promise((_resolve, reject) => {
          capturedSignal = options?.signal;
          // Simulate real fetch/AbortSignal: abort rejects instead of hanging forever,
          // otherwise shared refreshProject pending flags stay permanently occupied, contaminating subsequent same-file
          // cases by indefinitely queuing refreshProject calls.
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
    // Regression: initial loads now share refreshProject cancellation domains; rapid A→B switches may leave A responses
    // settling after switches (abort/response settlement are async; response-first network layers do not reject).
    // A must neither write currentProject nor lose/stall B initial loads.
    let resolveA!: (value: Awaited<ReturnType<typeof API.getProject>>) => void;
    const pendingA = new Promise<Awaited<ReturnType<typeof API.getProject>>>((res) => {
      resolveA = res;
    });
    vi.spyOn(API, "getProject").mockImplementation((name) =>
      name === "A"
        ? pendingA
        : Promise.resolve({ project: { title: "B-数据" } as never, scripts: {} }),
    );

    // Final states alone cannot refute late overwrites; even wrongly written A data could later be overwritten by B.
    // Subscribe throughout and assert A data never enters stores.
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
      expect(API.getProject).toHaveBeenCalledWith("A", { signal: expect.any(AbortSignal) });
    });

    act(() => navigate("/app/projects/B"));
    // A responses settle after switches complete.
    await act(async () => {
      resolveA({ project: { title: "A-迟到数据" } as never, scripts: {} });
      await pendingA;
    });

    await waitFor(() => {
      const s = useProjectsStore.getState();
      expect(s.currentProjectName).toBe("B");
      expect(s.currentProjectData?.title).toBe("B-数据");
      // B initial loads finish normally without A occupying pending/queue slots and leaving Loading.
      expect(s.projectDetailLoading).toBe(false);
    });
    unsubscribe();
    expect(seenTitles).not.toContain("A-迟到数据");
  });

  it("keeps project name when loading project details fails", async () => {
    vi.spyOn(API, "getProject").mockRejectedValue(new Error("network"));

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
    // Regression: AuthGuard renders in nested routes; relative /login becomes
    // /app/projects/demo/login (unmatched 404); absolute ~/login reaches /login.
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
    // Start uninitialized so root ConfigStatusLoader exercises actual fetch/retry logic.
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    // Failed config fetching keeps stores uninitialized, making loaders retry with backoff.
    vi.spyOn(API, "getProviders").mockRejectedValue(new Error("backend not ready"));
    vi.spyOn(API, "listCustomProviders").mockRejectedValue(new Error("backend not ready"));
    vi.spyOn(API, "getSystemConfig").mockRejectedValue(new Error("backend not ready"));

    renderAt("/app/projects");

    // Initial fetch on mount.
    await vi.advanceTimersByTimeAsync(0);
    expect(API.getProviders).toHaveBeenCalledTimes(1);

    // First backoff retry (800ms).
    await vi.advanceTimersByTimeAsync(800);
    expect(API.getProviders).toHaveBeenCalledTimes(2);

    // Second backoff retry (after 1600ms).
    await vi.advanceTimersByTimeAsync(1600);
    expect(API.getProviders).toHaveBeenCalledTimes(3);
  });
});
