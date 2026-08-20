import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
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
  useProjectsStore.setState(useProjectsStore.getInitialState(), true);
  useAssistantStore.setState(useAssistantStore.getInitialState(), true);
}

describe("AppRoutes", () => {
  beforeEach(() => {
    resetStores();
    useAuthStore.setState({ isAuthenticated: true, isLoading: false });
    // ConfigStatusLoader 在 AppRoutes 中始终挂载；预置 initialized 让其 fetch() 短路，
    // 路由测试无需关心配置状态，也避免触发未 mock 的供应商接口与退避重试。
    useConfigStatusStore.setState({ initialized: true });
    vi.restoreAllMocks();
  });

  afterEach(() => {
    // 个别用例切到 fake timers,统一还原,避免污染其它用例。
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
    expect(screen.getByText("404")).toBeInTheDocument();
    expect(screen.getByText("页面未找到")).toBeInTheDocument();
  });

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
    // 回归：演示项目的常量随 `t` 重灌，但这条依赖不能落到真实项目的加载 effect 上——
    // 否则切语言会先清空 store 闪一次空态，还连带清掉 Agent 会话状态。
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
    // 回归：首屏加载收编进 refreshProject 的取消域后，A → B 快速切换时 A 的响应可能
    // 在切换完成之后才落定（abort 与响应落定同为异步，响应先到时网络层不会 reject）。
    // 它既不该写回 currentProject，也不该让 B 自己的首屏加载失踪或停在加载态。
    let resolveA!: (value: Awaited<ReturnType<typeof API.getProject>>) => void;
    const pendingA = new Promise<Awaited<ReturnType<typeof API.getProject>>>((res) => {
      resolveA = res;
    });
    vi.spyOn(API, "getProject").mockImplementation((name) =>
      name === "A"
        ? pendingA
        : Promise.resolve({ project: { title: "B-数据" } as never, scripts: {} }),
    );

    // 只看最终态不足以证伪「迟到覆盖」——A 的数据即便写进去了，也会被随后跑完的 B 盖回
    // 去。订阅整段过程，断言 A 的数据一次都没有落进 store。
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
    // 切换完成后 A 的响应才落定
    await act(async () => {
      resolveA({ project: { title: "A-迟到数据" } as never, scripts: {} });
      await pendingA;
    });

    await waitFor(() => {
      const s = useProjectsStore.getState();
      expect(s.currentProjectName).toBe("B");
      expect(s.currentProjectData?.title).toBe("B-数据");
      // B 的首屏加载正常收口，不因 A 占着在途/排队名额而停在加载态
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
    // 回归：AuthGuard 渲染在 nest 路由内，相对的 /login 会被拼成
    // /app/projects/demo/login（无匹配 → 404）；用 ~/login 绝对路径才落到 /login。
    expect(await screen.findByTestId("login-page")).toBeInTheDocument();
    expect(screen.queryByText("404")).not.toBeInTheDocument();
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
    // 从未初始化起步,让根级 ConfigStatusLoader 真正执行 fetch/重试逻辑
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    // 让配置拉取失败 → store 保持未初始化 → loader 按退避重试
    vi.spyOn(API, "getProviders").mockRejectedValue(new Error("backend not ready"));
    vi.spyOn(API, "listCustomProviders").mockRejectedValue(new Error("backend not ready"));
    vi.spyOn(API, "getSystemConfig").mockRejectedValue(new Error("backend not ready"));

    renderAt("/app/projects");

    // 挂载即首次拉取
    await vi.advanceTimersByTimeAsync(0);
    expect(API.getProviders).toHaveBeenCalledTimes(1);

    // 第一次退避重试(800ms)
    await vi.advanceTimersByTimeAsync(800);
    expect(API.getProviders).toHaveBeenCalledTimes(2);

    // 第二次退避重试(再 1600ms)
    await vi.advanceTimersByTimeAsync(1600);
    expect(API.getProviders).toHaveBeenCalledTimes(3);
  });
});
