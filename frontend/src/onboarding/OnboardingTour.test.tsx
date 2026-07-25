import { act, render, waitFor } from "@testing-library/react";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { API } from "@/api";
import { useAuthStore } from "@/stores/auth-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { ONBOARDING_ANCHORS } from "./anchors";
import { DEMO_PROJECT_NAME, DEMO_SCRIPTED_EPISODE } from "./demo-project";
import { OnboardingTour } from "./OnboardingTour";

function renderAt(path: string) {
  const { hook } = memoryLocation({ path });
  return render(
    <Router hook={hook}>
      <OnboardingTour />
    </Router>,
  );
}

function renderWithNavigation(path: string) {
  const { hook, navigate } = memoryLocation({ path });
  const view = render(
    <Router hook={hook}>
      <OnboardingTour />
    </Router>,
  );
  return { ...view, navigate };
}

function popoverTitle(): string | null {
  return document.querySelector(".driver-popover-title")?.textContent ?? null;
}

describe("OnboardingTour", () => {
  beforeEach(() => {
    useOnboardingStore.setState(useOnboardingStore.getInitialState(), true);
    useAuthStore.setState({ isAuthenticated: true });
    vi.spyOn(API, "markOnboardingSeen").mockResolvedValue({ seen: true });
  });

  afterEach(async () => {
    await i18n.changeLanguage("zh");
  });

  it("opens the tour on the first visit to the main interface", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/app/projects");

    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));
  });

  it("stays out of the way once the tour has been seen", async () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: true });

    renderAt("/app/projects");

    await waitFor(() => expect(status).toHaveBeenCalled());
    expect(popoverTitle()).toBeNull();
  });

  it("does not run before the user reaches the main interface", () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    useAuthStore.setState({ isAuthenticated: false });

    renderAt("/app/projects");

    expect(status).not.toHaveBeenCalled();
    expect(popoverTitle()).toBeNull();
  });

  it("does not run on the login page", () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/login");

    expect(status).not.toHaveBeenCalled();
  });

  it("does not run on an unmatched route (404)", () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/some/unknown/path");

    expect(status).not.toHaveBeenCalled();
    expect(popoverTitle()).toBeNull();
  });

  it("does not run on an unmatched route that merely shares the /app prefix", () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/application");

    expect(status).not.toHaveBeenCalled();
    expect(popoverTitle()).toBeNull();
  });

  it("does not run on an unregistered sub-path under a single-page app route", () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/app/settings/unknown");

    expect(status).not.toHaveBeenCalled();
    expect(popoverTitle()).toBeNull();
  });

  it("does not run on an unregistered sub-path inside a project workspace", () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/app/projects/my-novel/unknown");

    expect(status).not.toHaveBeenCalled();
    expect(popoverTitle()).toBeNull();
  });

  it("runs on a registered sub-path inside a project workspace", async () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/app/projects/my-novel/characters");

    await waitFor(() => expect(status).toHaveBeenCalled());
  });

  it("runs on the source files list page, which has no filename segment", async () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/app/projects/my-novel/source");

    await waitFor(() => expect(status).toHaveBeenCalled());
  });

  it("runs on the project settings page", async () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/app/projects/my-novel/settings");

    await waitFor(() => expect(status).toHaveBeenCalled());
  });

  it("runs on a case-variant URL that wouter itself matches case-insensitively", async () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/APP/Projects/my-novel/Characters");

    await waitFor(() => expect(status).toHaveBeenCalled());
  });

  it("runs on a URL with a trailing slash that wouter itself matches", async () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/app/settings/");

    await waitFor(() => expect(status).toHaveBeenCalled());
  });

  it("marks the tour as seen when it is closed, and does not reopen it", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/app/projects");
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));

    document.querySelector<HTMLElement>(".driver-popover-close-btn")?.click();

    await waitFor(() => expect(API.markOnboardingSeen).toHaveBeenCalledTimes(1));
    expect(popoverTitle()).toBeNull();
    expect(useOnboardingStore.getState().seen).toBe(true);
  });

  it("replays the tour on request without writing the flag again", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: true });

    renderAt("/app/projects");
    await waitFor(() => expect(API.getOnboardingStatus).toHaveBeenCalled());

    useOnboardingStore.getState().start();

    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));
    document.querySelector<HTMLElement>(".driver-popover-close-btn")?.click();

    await waitFor(() => expect(popoverTitle()).toBeNull());
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();
  });

  it("keeps its place when the interface language changes mid-tour", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    // 第 2 步指向大厅的「新建项目」按钮。这里只渲染引导挂载点、没有大厅，补一个锚点
    // 元素，测的才是保位而不是锚点缺席时的降级。
    const anchor = document.createElement("button");
    anchor.setAttribute("data-onboarding", ONBOARDING_ANCHORS.lobbyCreateProject);
    document.body.appendChild(anchor);

    renderAt("/app/projects");
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));
    document.querySelector<HTMLElement>(".driver-popover-next-btn")?.click();
    expect(popoverTitle()).toBe("从这里新建项目");

    await act(async () => {
      await i18n.changeLanguage("en");
    });

    await waitFor(() => expect(popoverTitle()).toBe("Start a project here"));
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();
    anchor.remove();
  });

  it("navigates to the lobby before running the tour when replayed from another main-UI route", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: true });

    // 欢迎步骤本身 anchor 为 null、居中显示，跳没跳转它都会出现——单看气泡标题测
    // 不出跳转是否真的发生了，需要 `record: true` 记录的导航历史直接断言落点。
    const { hook, history } = memoryLocation({ path: "/app/settings", record: true });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(API.getOnboardingStatus).toHaveBeenCalled());
    expect(popoverTitle()).toBeNull();

    act(() => useOnboardingStore.getState().start());

    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));
    expect(history.at(-1)).toBe("/app/projects");
  });

  /** 大厅三步的锚点。没有大厅页面可渲染，手动补上元素，测的是跳页而不是锚点降级。 */
  function mountLobbyAnchors(): HTMLElement[] {
    const names = [
      ONBOARDING_ANCHORS.lobbyCreateProject,
      ONBOARDING_ANCHORS.lobbyDemoCard,
      ONBOARDING_ANCHORS.lobbySettings,
    ];
    return names.map((name) => {
      const el = document.createElement("button");
      el.setAttribute("data-onboarding", name);
      document.body.appendChild(el);
      return el;
    });
  }

  /** 设置页两步的锚点。 */
  function mountSettingsAnchors(): HTMLElement[] {
    const names = [ONBOARDING_ANCHORS.settingsProviders, ONBOARDING_ANCHORS.settingsAgent];
    return names.map((name) => {
      const el = document.createElement("button");
      el.setAttribute("data-onboarding", name);
      document.body.appendChild(el);
      return el;
    });
  }

  function click(selector: string): void {
    document.querySelector<HTMLElement>(selector)?.click();
  }

  it("crosses from the lobby into settings when the tour reaches the provider step", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    const lobbyAnchors = mountLobbyAnchors();
    const settingsAnchor = document.createElement("button");
    settingsAnchor.setAttribute("data-onboarding", ONBOARDING_ANCHORS.settingsProviders);
    document.body.appendChild(settingsAnchor);

    const { hook, history } = memoryLocation({ path: "/app/projects", record: true });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));

    click(".driver-popover-next-btn"); // → 新建项目入口
    click(".driver-popover-next-btn"); // → 演示卡
    click(".driver-popover-next-btn"); // → 设置入口（仍在大厅）
    click(".driver-popover-next-btn"); // → 媒体供应商（跨页到设置）

    await waitFor(() => expect(popoverTitle()).toBe("配置一个媒体供应商"));
    expect(history.at(-1)).toBe("/app/settings");
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();
    [...lobbyAnchors, settingsAnchor].forEach((el) => el.remove());
  });

  it("crosses back into the lobby when the tour steps backwards out of settings", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    const lobbyAnchors = mountLobbyAnchors();
    const settingsAnchor = document.createElement("button");
    settingsAnchor.setAttribute("data-onboarding", ONBOARDING_ANCHORS.settingsProviders);
    document.body.appendChild(settingsAnchor);

    const { hook, history } = memoryLocation({ path: "/app/projects", record: true });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));

    click(".driver-popover-next-btn");
    click(".driver-popover-next-btn");
    click(".driver-popover-next-btn");
    click(".driver-popover-next-btn"); // → 媒体供应商（跨页到设置）
    await waitFor(() => expect(popoverTitle()).toBe("配置一个媒体供应商"));
    expect(history.at(-1)).toBe("/app/settings");

    click(".driver-popover-prev-btn"); // ← 设置入口（跨页回大厅）

    await waitFor(() => expect(popoverTitle()).toBe("供应商配置在设置里"));
    expect(history.at(-1)).toBe("/app/projects");
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();
    [...lobbyAnchors, settingsAnchor].forEach((el) => el.remove());
  });

  it("pulls back to the lobby if another tour route is reached mid-way through the interactive demo-card step", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    const lobbyAnchors = mountLobbyAnchors();

    const { hook, history, navigate } = memoryLocation({ path: "/app/projects", record: true });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));

    click(".driver-popover-next-btn"); // → 新建项目入口
    click(".driver-popover-next-btn"); // → 演示卡（interactive 步，锚点仍在大厅）
    await waitFor(() => expect(popoverTitle()).toBe("项目推进后长这样"));

    // 演示卡步是 interactive，允许用户点卡片本身离开大厅进工作台；但如果落点是引导
    // 覆盖的另一个路由（如顶栏「设置」），不该被这条豁免一并放过——否则 driver 停在
    // 演示卡步却找不到锚点，会降级成与设置页内容不符的居中气泡。
    act(() => navigate("/app/settings"));

    await waitFor(() => expect(history.at(-1)).toBe("/app/projects"));
    expect(popoverTitle()).toBe("项目推进后长这样");

    lobbyAnchors.forEach((el) => el.remove());
  });

  it("degrades to a centered popover when the settings-step anchor never mounts", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const lobbyAnchors = mountLobbyAnchors();

    const { hook } = memoryLocation({ path: "/app/projects" });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));

    click(".driver-popover-next-btn");
    click(".driver-popover-next-btn");
    click(".driver-popover-next-btn");
    click(".driver-popover-next-btn"); // 没有渲染设置页，锚点不存在——等待超时后应当降级为居中气泡而不是卡住

    await waitFor(() => expect(popoverTitle()).toBe("配置一个媒体供应商"), { timeout: 3000 });
    // 讲解照常进行，丢的只是高亮——driver 顶上自己的占位元素，气泡回到屏幕中央。
    expect(document.getElementById("driver-dummy-element")?.classList.contains("driver-active-element")).toBe(
      true,
    );
    expect(warn).toHaveBeenCalledWith(expect.stringContaining(ONBOARDING_ANCHORS.settingsProviders));

    warn.mockRestore();
    lobbyAnchors.forEach((el) => el.remove());
  });

  it("still marks the tour as seen when skipped mid-way through the settings steps", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    const lobbyAnchors = mountLobbyAnchors();
    const settingsAnchor = document.createElement("button");
    settingsAnchor.setAttribute("data-onboarding", ONBOARDING_ANCHORS.settingsProviders);
    document.body.appendChild(settingsAnchor);

    const { hook } = memoryLocation({ path: "/app/projects" });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));

    click(".driver-popover-next-btn");
    click(".driver-popover-next-btn");
    click(".driver-popover-next-btn");
    click(".driver-popover-next-btn");
    await waitFor(() => expect(popoverTitle()).toBe("配置一个媒体供应商"));

    click(".arc-tour-skip-btn");

    await waitFor(() => expect(API.markOnboardingSeen).toHaveBeenCalledTimes(1));
    expect(popoverTitle()).toBeNull();
    expect(useOnboardingStore.getState().seen).toBe(true);
    [...lobbyAnchors, settingsAnchor].forEach((el) => el.remove());
  });

  /** 工作台四步的锚点。同样没有工作台页面可渲染，补上元素测的是跨页串联而不是降级。 */
  function mountWorkbenchAnchors(): HTMLElement[] {
    const names = [
      ONBOARDING_ANCHORS.workbenchOverview,
      ONBOARDING_ANCHORS.workbenchLorebook,
      ONBOARDING_ANCHORS.workbenchTimeline,
      ONBOARDING_ANCHORS.workbenchExport,
    ];
    return names.map((name) => {
      const el = document.createElement("button");
      el.setAttribute("data-onboarding", name);
      document.body.appendChild(el);
      return el;
    });
  }

  const DEMO_WORKBENCH = `/app/projects/${DEMO_PROJECT_NAME}`;
  const DEMO_EPISODE = `${DEMO_WORKBENCH}/episodes/${DEMO_SCRIPTED_EPISODE}`;

  it("walks all eleven steps from the lobby through the demo workbench and back to the lobby", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    const anchors = [...mountLobbyAnchors(), ...mountSettingsAnchors(), ...mountWorkbenchAnchors()];

    const { hook, history } = memoryLocation({ path: "/app/projects", record: true });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));

    // 每一步：标题 + 该步走完后当前所在路由。跨页步骤的落点由 route 驱动。
    const expected: [string, string][] = [
      ["从这里新建项目", "/app/projects"],
      ["项目推进后长这样", "/app/projects"],
      ["供应商配置在设置里", "/app/projects"],
      ["配置一个媒体供应商", "/app/settings"],
      ["接入 Agent", "/app/settings"],
      ["项目全景在概述画布", DEMO_WORKBENCH],
      ["设定集统一管角色、场景、道具", `${DEMO_WORKBENCH}/characters`],
      ["一集的活都在分镜画布上", DEMO_EPISODE],
      ["出片后从顶栏导出", DEMO_EPISODE],
      ["轮到你了", "/app/projects"],
    ];

    for (const [title, route] of expected) {
      click(".driver-popover-next-btn");
      await waitFor(() => expect(popoverTitle()).toBe(title));
      expect(history.at(-1)).toBe(route);
    }

    // 收尾之前一次都没写过标记——中途没有哪一步误判成结束。
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();

    click(".driver-popover-next-btn"); // 收尾气泡上是「完成」
    await waitFor(() => expect(API.markOnboardingSeen).toHaveBeenCalledTimes(1));
    expect(popoverTitle()).toBeNull();
    expect(useOnboardingStore.getState().seen).toBe(true);

    anchors.forEach((el) => el.remove());
  }, 20_000);

  it("suspends the tour when the user follows the demo card into the workbench, and resumes in place on return", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    const anchors = mountLobbyAnchors();

    const { hook, history, navigate } = memoryLocation({ path: "/app/projects", record: true });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));

    click(".driver-popover-next-btn");
    click(".driver-popover-next-btn"); // → 演示卡（interactive）
    await waitFor(() => expect(popoverTitle()).toBe("项目推进后长这样"));

    // 顺着这一步给的入口点进演示工作台：不该被拽回大厅，气泡先收起让用户自己逛。
    act(() => navigate(DEMO_WORKBENCH));

    await waitFor(() => expect(popoverTitle()).toBeNull());
    expect(history.at(-1)).toBe(DEMO_WORKBENCH);
    // 工作台的子路由同样算「还在落点里」。
    act(() => navigate(`${DEMO_WORKBENCH}/characters`));
    expect(popoverTitle()).toBeNull();
    expect(history.at(-1)).toBe(`${DEMO_WORKBENCH}/characters`);

    // 逛完回到大厅，引导从演示卡这一步原位续讲，不重头也不记已看过。
    act(() => navigate("/app/projects"));

    await waitFor(() => expect(popoverTitle()).toBe("项目推进后长这样"));
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();

    anchors.forEach((el) => el.remove());
  });

  it("still marks the tour as seen when skipped inside the read-only demo workbench", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    const anchors = [...mountLobbyAnchors(), ...mountSettingsAnchors(), ...mountWorkbenchAnchors()];

    const { hook, history } = memoryLocation({ path: "/app/projects", record: true });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));

    for (let i = 0; i < 6; i++) click(".driver-popover-next-btn"); // → 概述画布（工作台第一步）
    await waitFor(() => expect(popoverTitle()).toBe("项目全景在概述画布"));
    expect(history.at(-1)).toBe(DEMO_WORKBENCH);

    click(".arc-tour-skip-btn");

    // 只读闸门对 /onboarding/seen 有窄豁免，工作台里跳过照样写得进去。
    await waitFor(() => expect(API.markOnboardingSeen).toHaveBeenCalledTimes(1));
    expect(popoverTitle()).toBeNull();
    expect(useOnboardingStore.getState().seen).toBe(true);

    anchors.forEach((el) => el.remove());
  }, 20_000);

  it("replays the full eleven steps from the settings entry", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: true });
    const anchors = [...mountLobbyAnchors(), ...mountSettingsAnchors(), ...mountWorkbenchAnchors()];

    // 重看入口在设置页，起步得先跨回大厅——重看和首弹共用同一条 11 步大纲。
    const { hook, history } = memoryLocation({ path: "/app/settings", record: true });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(API.getOnboardingStatus).toHaveBeenCalled());

    act(() => useOnboardingStore.getState().start());
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));
    expect(history.at(-1)).toBe("/app/projects");

    for (let i = 0; i < 10; i++) click(".driver-popover-next-btn");
    await waitFor(() => expect(popoverTitle()).toBe("轮到你了"));
    expect(history.at(-1)).toBe("/app/projects");

    click(".driver-popover-next-btn");
    await waitFor(() => expect(popoverTitle()).toBeNull());
    // 重看不改写标记——本来就已经是已看过。
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();

    anchors.forEach((el) => el.remove());
  }, 20_000);

  it("issues no requests of its own beyond the seen flag across the whole eleven-step run", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    // 引导自身不该发任何别的请求：没有生成调用、没有入队、没有项目写入。挂载点之外
    // 的页面组件不在本用例里，这里守的是引导这条链路自己。
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const anchors = [...mountLobbyAnchors(), ...mountSettingsAnchors(), ...mountWorkbenchAnchors()];

    const { hook } = memoryLocation({ path: "/app/projects" });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));

    for (let i = 0; i < 10; i++) click(".driver-popover-next-btn");
    await waitFor(() => expect(popoverTitle()).toBe("轮到你了"));
    click(".driver-popover-next-btn");
    await waitFor(() => expect(API.markOnboardingSeen).toHaveBeenCalledTimes(1));

    // 两个查询/写标记的端点都被 spy 挡在 fetch 之前，所以这里应当一次都没到网络。
    expect(fetchMock).not.toHaveBeenCalled();

    vi.unstubAllGlobals();
    anchors.forEach((el) => el.remove());
  }, 20_000);

  it("takes the tour down when the user navigates back to the login page mid-tour", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    const { navigate } = renderWithNavigation("/app/projects");
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));

    act(() => navigate("/login"));

    expect(popoverTitle()).toBeNull();
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();
  });

  it("takes the tour down when the mount point unmounts", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    const { unmount } = renderAt("/app/projects");
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));

    unmount();

    expect(document.querySelector(".driver-popover")).toBeNull();
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();
  });
});
