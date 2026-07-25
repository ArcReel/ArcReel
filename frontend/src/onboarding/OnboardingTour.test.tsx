import { act, render, waitFor } from "@testing-library/react";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { API } from "@/api";
import { useAuthStore } from "@/stores/auth-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { ONBOARDING_ANCHORS } from "./anchors";
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
