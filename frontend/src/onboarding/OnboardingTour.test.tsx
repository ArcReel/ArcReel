import { act, render, waitFor } from "@testing-library/react";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { API } from "@/api";
import { ROUTE_APP_ASSETS } from "@/app-routes";
import { useAuthStore } from "@/stores/auth-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { ONBOARDING_ANCHORS, type OnboardingAnchor } from "./anchors";
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

    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));
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

  it("runs on the project-level episodes view, which has no episode segment", async () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/app/projects/my-novel/episodes");

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
    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));

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

    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));
    document.querySelector<HTMLElement>(".driver-popover-close-btn")?.click();

    await waitFor(() => expect(popoverTitle()).toBeNull());
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();
  });

  it("keeps its place when the interface language changes mid-tour", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    // Step 2 targets lobby New project. Rendering onboarding alone lacks lobbies, so add anchors
    // to test positioning preservation rather than missing-anchor degradation.
    const anchor = document.createElement("button");
    anchor.setAttribute("data-onboarding", ONBOARDING_ANCHORS.lobbyCreateProject);
    document.body.appendChild(anchor);

    renderAt("/app/projects");
    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));
    document.querySelector<HTMLElement>(".driver-popover-next-btn")?.click();
    expect(popoverTitle()).toBe("新建项目");

    await act(async () => {
      await i18n.changeLanguage("en");
    });

    await waitFor(() => expect(popoverTitle()).toBe("Create a project"));
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();
    anchor.remove();
  });

  it("navigates to the lobby before running the tour when replayed from another main-UI route", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: true });

    // Welcome has null anchors/centered popovers regardless of navigation; titles alone cannot prove
    // route changes. Assert destinations through navigation histories with `record: true`.
    const { hook, history } = memoryLocation({ path: "/app/settings", record: true });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(API.getOnboardingStatus).toHaveBeenCalled());
    expect(popoverTitle()).toBeNull();

    act(() => useOnboardingStore.getState().start());

    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));
    expect(history.at(-1)).toBe("/app/projects");
  });

  /**
   * Supply anchor elements manually; no real pages here, testing navigation sequencing rather than missing-anchor fallback.
   * `anchors.test.tsx` separately verifies actual UI anchor presence.
   */
  function mountAnchors(...names: OnboardingAnchor[]): HTMLElement[] {
    return names.map((name) => {
      const el = document.createElement("button");
      el.setAttribute("data-onboarding", name);
      document.body.appendChild(el);
      return el;
    });
  }

  /** Three lobby-step anchors. */
  function mountLobbyAnchors(): HTMLElement[] {
    return mountAnchors(
      ONBOARDING_ANCHORS.lobbyCreateProject,
      ONBOARDING_ANCHORS.lobbyDemoCard,
      ONBOARDING_ANCHORS.lobbySettings,
    );
  }

  /** Two settings-step anchors. */
  function mountSettingsAnchors(): HTMLElement[] {
    return mountAnchors(ONBOARDING_ANCHORS.settingsProviders, ONBOARDING_ANCHORS.settingsAgent);
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
    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));

    click(".driver-popover-next-btn"); // → New-project entry.
    click(".driver-popover-next-btn"); // → Settings entry, still lobby.
    click(".driver-popover-next-btn"); // → Providers, crossing to settings.

    await waitFor(() => expect(popoverTitle()).toBe("配置供应商"));
    // Include section queries, required for provider content.
    expect(history.at(-1)).toBe("/app/settings?section=providers");
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
    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));

    click(".driver-popover-next-btn");
    click(".driver-popover-next-btn");
    click(".driver-popover-next-btn"); // → Providers, crossing to settings.
    await waitFor(() => expect(popoverTitle()).toBe("配置供应商"));
    expect(history.at(-1)).toBe("/app/settings?section=providers");

    click(".driver-popover-prev-btn"); // ← Settings entry, returning lobby.

    await waitFor(() => expect(popoverTitle()).toBe("设置"));
    expect(history.at(-1)).toBe("/app/projects");
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();
    [...lobbyAnchors, settingsAnchor].forEach((el) => el.remove());
  });

  it("pulls back to the lobby if another tour route is reached mid-way through the interactive demo-card step", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    const anchors = [...mountLobbyAnchors(), ...mountSettingsAnchors()];

    const { hook, history, navigate } = memoryLocation({ path: "/app/projects", record: true });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));

    for (let i = 0; i < 5; i++) click(".driver-popover-next-btn"); // → Demo cards, interactive lobby anchors.
    await waitFor(() => expect(popoverTitle()).toBe("演示项目"));

    // Interactive demo steps permit card navigation into workbenches, but other onboarding-covered
    // destinations such as header Settings must not share exemptions, or drivers retain demo-card
    // steps without anchors, degrading to centered popovers contradicting settings content.
    act(() => navigate("/app/settings"));

    await waitFor(() => expect(history.at(-1)).toBe("/app/projects"));
    expect(popoverTitle()).toBe("演示项目");

    anchors.forEach((el) => el.remove());
  }, 20_000);

  it("pulls back to the lobby if a route outside the tour is reached mid-way through the interactive demo-card step", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    const anchors = [...mountLobbyAnchors(), ...mountSettingsAnchors()];

    const { hook, history, navigate } = memoryLocation({ path: "/app/projects", record: true });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));

    for (let i = 0; i < 5; i++) click(".driver-popover-next-btn"); // → Interactive demo cards.
    await waitFor(() => expect(popoverTitle()).toBe("演示项目"));

    // Return from every non-target destination, even outside tour coverage: libraries are main routes
    // outside `tourRoutes`; exempt only declared `interactiveTarget`.
    act(() => navigate(ROUTE_APP_ASSETS));

    await waitFor(() => expect(history.at(-1)).toBe("/app/projects"));
    expect(popoverTitle()).toBe("演示项目");
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();

    anchors.forEach((el) => el.remove());
  }, 20_000);

  it("switches the settings pane back to providers when stepping backwards from the agent step", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    const anchors = [...mountLobbyAnchors(), ...mountSettingsAnchors()];

    const { hook, history } = memoryLocation({ path: "/app/projects", record: true });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));

    for (let i = 0; i < 4; i++) click(".driver-popover-next-btn"); // → Configure Agent.
    await waitFor(() => expect(popoverTitle()).toBe("配置 ArcReel Agent"));
    expect(history.at(-1)).toBe("/app/settings?section=arcreel-agent");

    // Both steps use /app/settings with unchanged pathname; section queries must switch content
    // back to Providers instead of retaining Agent during provider explanations, likewise forward.
    click(".driver-popover-prev-btn");

    await waitFor(() => expect(popoverTitle()).toBe("配置供应商"));
    expect(history.at(-1)).toBe("/app/settings?section=providers");
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();

    anchors.forEach((el) => el.remove());
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
    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));

    click(".driver-popover-next-btn");
    click(".driver-popover-next-btn");
    click(".driver-popover-next-btn"); // Settings unrendered, anchors absent; timeout must center popovers rather than stall.

    await waitFor(() => expect(popoverTitle()).toBe("配置供应商"), { timeout: 3000 });
    // Explanations continue without highlights; drivers use placeholders/centered popovers.
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
    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));

    click(".driver-popover-next-btn");
    click(".driver-popover-next-btn");
    click(".driver-popover-next-btn");
    await waitFor(() => expect(popoverTitle()).toBe("配置供应商"));

    click(".arc-tour-skip-btn");

    await waitFor(() => expect(API.markOnboardingSeen).toHaveBeenCalledTimes(1));
    expect(popoverTitle()).toBeNull();
    expect(useOnboardingStore.getState().seen).toBe(true);
    [...lobbyAnchors, settingsAnchor].forEach((el) => el.remove());
  });

  /** Five demo-workbench anchors. */
  function mountWorkbenchAnchors(): HTMLElement[] {
    return mountAnchors(
      ONBOARDING_ANCHORS.workbenchOverview,
      ONBOARDING_ANCHORS.workbenchAgent,
      ONBOARDING_ANCHORS.workbenchLorebook,
      ONBOARDING_ANCHORS.workbenchTimeline,
      ONBOARDING_ANCHORS.workbenchExport,
    );
  }

  /** All twelve-step anchors for complete-sequence tests. */
  function mountAllAnchors(): HTMLElement[] {
    return [...mountLobbyAnchors(), ...mountSettingsAnchors(), ...mountWorkbenchAnchors()];
  }

  const DEMO_WORKBENCH = `/app/projects/${DEMO_PROJECT_NAME}`;
  const DEMO_EPISODE = `${DEMO_WORKBENCH}/episodes/${DEMO_SCRIPTED_EPISODE}`;

  it("walks all twelve steps from the lobby through the demo workbench and back to the lobby", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    const anchors = mountAllAnchors();

    const { hook, history } = memoryLocation({ path: "/app/projects", record: true });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));

    // Each step: titles/current routes after completion. route drives cross-page destinations.
    const expected: [string, string][] = [
      ["新建项目", "/app/projects"],
      ["设置", "/app/projects"],
      ["配置供应商", "/app/settings?section=providers"],
      ["配置 ArcReel Agent", "/app/settings?section=arcreel-agent"],
      ["演示项目", "/app/projects"],
      ["Agent", DEMO_WORKBENCH],
      ["项目概览", DEMO_WORKBENCH],
      ["角色、场景与道具", `${DEMO_WORKBENCH}/characters`],
      ["分镜", `${DEMO_EPISODE}?view=board`],
      ["导出项目", `${DEMO_EPISODE}?view=board`],
      ["开始你的第一个项目", "/app/projects"],
    ];

    for (const [title, route] of expected) {
      click(".driver-popover-next-btn");
      await waitFor(() => expect(popoverTitle()).toBe(title));
      expect(history.at(-1)).toBe(route);
    }

    // No markers written before closing; no intermediate step falsely counts as finished.
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();

    click(".driver-popover-next-btn"); // Closing bubbles say Finish.
    await waitFor(() => expect(API.markOnboardingSeen).toHaveBeenCalledTimes(1));
    expect(popoverTitle()).toBeNull();
    expect(useOnboardingStore.getState().seen).toBe(true);

    anchors.forEach((el) => el.remove());
  }, 20_000);

  it("advances straight into the workbench segment when the user follows the demo card in", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    const anchors = mountAllAnchors();

    const { hook, history, navigate } = memoryLocation({ path: "/app/projects", record: true });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));

    for (let i = 0; i < 5; i++) click(".driver-popover-next-btn"); // → Demo cards (interactive).
    await waitFor(() => expect(popoverTitle()).toBe("演示项目"));

    // Follow step entries into demo workbenches: never force lobbies, naturally advancing to first workbench steps (Agent);
    // card clicks and Next share destinations.
    act(() => navigate(DEMO_WORKBENCH));

    await waitFor(() => expect(popoverTitle()).toBe("Agent"));
    expect(history.at(-1)).toBe(DEMO_WORKBENCH);
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();

    anchors.forEach((el) => el.remove());
  }, 20_000);

  it("still marks the tour as seen when skipped inside the read-only demo workbench", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    const anchors = mountAllAnchors();

    const { hook, history } = memoryLocation({ path: "/app/projects", record: true });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));

    for (let i = 0; i < 6; i++) click(".driver-popover-next-btn"); // → Agent (first workbench step).
    await waitFor(() => expect(popoverTitle()).toBe("Agent"));
    expect(history.at(-1)).toBe(DEMO_WORKBENCH);

    click(".arc-tour-skip-btn");

    // Narrow read-only /onboarding/seen exemptions permit Skip markers within workbenches.
    await waitFor(() => expect(API.markOnboardingSeen).toHaveBeenCalledTimes(1));
    expect(popoverTitle()).toBeNull();
    expect(useOnboardingStore.getState().seen).toBe(true);

    anchors.forEach((el) => el.remove());
  }, 20_000);

  it("replays the full twelve steps from the settings entry", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: true });
    const anchors = mountAllAnchors();

    // Replay starts in settings and first crosses back to lobbies, sharing identical twelve-step outlines with first openings.
    const { hook, history } = memoryLocation({ path: "/app/settings", record: true });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(API.getOnboardingStatus).toHaveBeenCalled());

    act(() => useOnboardingStore.getState().start());
    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));
    expect(history.at(-1)).toBe("/app/projects");

    for (let i = 0; i < 11; i++) click(".driver-popover-next-btn");
    await waitFor(() => expect(popoverTitle()).toBe("开始你的第一个项目"));
    expect(history.at(-1)).toBe("/app/projects");

    click(".driver-popover-next-btn");
    await waitFor(() => expect(popoverTitle()).toBeNull());
    // Replay never rewrites already-seen markers.
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();

    anchors.forEach((el) => el.remove());
  }, 20_000);

  it("issues no requests of its own beyond the seen flag across the whole twelve-step run", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    // Onboarding must issue no other requests: no generation/enqueue/project writes. Page components outside mounting
    // points are absent here; guard onboarding paths themselves.
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const anchors = mountAllAnchors();

    const { hook } = memoryLocation({ path: "/app/projects" });
    render(
      <Router hook={hook}>
        <OnboardingTour />
      </Router>,
    );
    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));

    for (let i = 0; i < 11; i++) click(".driver-popover-next-btn");
    await waitFor(() => expect(popoverTitle()).toBe("开始你的第一个项目"));
    click(".driver-popover-next-btn");
    await waitFor(() => expect(API.markOnboardingSeen).toHaveBeenCalledTimes(1));

    // Both marker query/write endpoints are spied before fetch, so no requests should reach networks.
    expect(fetchMock).not.toHaveBeenCalled();

    vi.unstubAllGlobals();
    anchors.forEach((el) => el.remove());
  }, 20_000);

  it("takes the tour down when the user navigates back to the login page mid-tour", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    const { navigate } = renderWithNavigation("/app/projects");
    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));

    act(() => navigate("/login"));

    expect(popoverTitle()).toBeNull();
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();
  });

  it("takes the tour down when the mount point unmounts", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    const { unmount } = renderAt("/app/projects");
    await waitFor(() => expect(popoverTitle()).toBe("欢迎使用 ArcReel"));

    unmount();

    expect(document.querySelector(".driver-popover")).toBeNull();
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();
  });
});
