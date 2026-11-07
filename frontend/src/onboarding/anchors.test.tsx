/**
 * Verify each onboarding anchor actually exists in UIs.
 *
 * `RENDERERS` uses `Record<OnboardingAnchor, …>`; adding registered anchors without render scenarios
 * fails compilation. Types prevent name drift; cases prevent disappearing elements, neither relying on manual inspection.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import i18n from "@/i18n";
import { API } from "@/api";
import { GlobalHeader } from "@/components/layout/GlobalHeader";
import { OverviewCanvas } from "@/components/canvas/overview/OverviewCanvas";
import { CharactersPage } from "@/components/canvas/lorebook/CharactersPage";
import { TimelineCanvas } from "@/components/canvas/timeline/TimelineCanvas";
import { ProjectsPage } from "@/components/pages/ProjectsPage";
import { SystemConfigPage } from "@/components/pages/SystemConfigPage";
import { useAppStore } from "@/stores/app-store";
import { useCostStore } from "@/stores/cost-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { ONBOARDING_ANCHORS, type OnboardingAnchor } from "./anchors";
import { DemoAssistantPanel } from "./DemoAssistantPanel";
import {
  buildDemoProjectData,
  buildDemoScripts,
  DEMO_PROJECT_NAME,
  DEMO_SCRIPTED_EPISODE,
} from "./demo-project";
import { anchorSelector } from "./tour";

vi.mock("@/components/pages/CreateProjectModal", () => ({
  CreateProjectModal: () => <div data-testid="create-project-modal" />,
}));

// Heavy header/storyboard-canvas children are unrelated to anchors; stub their data dependencies.
vi.mock("@/components/usage/UsageHeaderEntry", () => ({ UsageHeaderEntry: () => null }));
vi.mock("@/components/canvas/timeline/ScriptReviewGate", async () => {
  const { scriptReviewGateMock } = await import("@/__mocks__/ScriptReviewGate");
  return scriptReviewGateMock();
});
vi.mock("@/components/canvas/timeline/ShotSplitView", () => ({ ShotSplitView: () => null }));

function renderLobby() {
  const { hook } = memoryLocation({ path: "/app/projects" });
  render(
    <Router hook={hook}>
      <ProjectsPage />
    </Router>,
  );
}

function renderSettings() {
  const { hook } = memoryLocation({ path: "/app/settings" });
  render(
    <Router hook={hook}>
      <SystemConfigPage />
    </Router>,
  );
}

// Four workbench steps target demos; anchor tests construct props from identical demo data, matching actual
// onboarding mount conditions without separately invented mocks.
const demoT = i18n.getFixedT("zh", "onboarding");

function renderWorkbenchHeader() {
  const { hook } = memoryLocation({ path: `/app/projects/${DEMO_PROJECT_NAME}` });
  render(
    <Router hook={hook}>
      <GlobalHeader />
    </Router>,
  );
}

/** Mount owning interfaces per anchor; shared assertions occur below. */
const RENDERERS: Record<OnboardingAnchor, () => void> = {
  [ONBOARDING_ANCHORS.lobbyCreateProject]: renderLobby,
  [ONBOARDING_ANCHORS.lobbySettings]: renderLobby,
  // Demo cards inject only during tours; open stores first.
  [ONBOARDING_ANCHORS.lobbyDemoCard]: () => {
    useOnboardingStore.setState({ active: true });
    renderLobby();
  },
  [ONBOARDING_ANCHORS.settingsProviders]: renderSettings,
  [ONBOARDING_ANCHORS.settingsAgent]: renderSettings,
  [ONBOARDING_ANCHORS.workbenchOverview]: () => {
    render(<OverviewCanvas projectName={DEMO_PROJECT_NAME} projectData={buildDemoProjectData(demoT)} readOnly />);
  },
  // Anchors attach to demo-only Agent panels; real panels do not mount in demos (anchors.ts).
  [ONBOARDING_ANCHORS.workbenchAgent]: () => {
    render(<DemoAssistantPanel />);
  },
  [ONBOARDING_ANCHORS.workbenchLorebook]: () => {
    render(
      <CharactersPage
        projectName={DEMO_PROJECT_NAME}
        characters={buildDemoProjectData(demoT).characters ?? {}}
        onGenerateCharacter={vi.fn()}
        readOnly
      />,
    );
  },
  [ONBOARDING_ANCHORS.workbenchTimeline]: () => {
    render(
      <TimelineCanvas
        view="board"
        onViewChange={vi.fn()}
        projectName={DEMO_PROJECT_NAME}
        episode={DEMO_SCRIPTED_EPISODE}
        hasDraft
        episodeScript={buildDemoScripts(demoT)[`E${DEMO_SCRIPTED_EPISODE}.json`] ?? null}
        projectData={buildDemoProjectData(demoT)}
      />,
    );
  },
  [ONBOARDING_ANCHORS.workbenchExport]: renderWorkbenchHeader,
};

describe("onboarding anchors", () => {
  beforeEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useAppStore.setState(useAppStore.getInitialState(), true);
    useOnboardingStore.setState(useOnboardingStore.getInitialState(), true);
    useCostStore.setState(useCostStore.getInitialState(), true);
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [] });
  });

  const CASES = Object.entries(RENDERERS) as [OnboardingAnchor, () => void][];

  it.each(CASES)("%s is present in the UI", async (anchor, mount) => {
    mount();

    await waitFor(() => {
      expect(document.querySelector(anchorSelector(anchor))).not.toBeNull();
    });
  });

  it("shows the demo card only while the tour is running", async () => {
    renderLobby();

    await waitFor(() => expect(API.listProjects).toHaveBeenCalled());
    expect(document.querySelector(anchorSelector(ONBOARDING_ANCHORS.lobbyDemoCard))).toBeNull();
  });

  it("adds a sample-project section above the empty lobby during the tour and removes only that section afterwards", async () => {
    useOnboardingStore.setState({ active: true });
    renderLobby();

    const section = await screen.findByRole("region", { name: "示例项目" });
    expect(section).toHaveTextContent("仅在引导期间显示");
    expect(screen.getByText("还没有项目")).toBeInTheDocument();

    act(() => useOnboardingStore.setState({ active: false }));

    expect(screen.queryByRole("region", { name: "示例项目" })).not.toBeInTheDocument();
    expect(screen.getByText("还没有项目")).toBeInTheDocument();
  });

  it("keeps the demo card out of the user's real project list", async () => {
    useOnboardingStore.setState({ active: true });
    renderLobby();

    // Posters/titles each appear once.
    expect(await screen.findAllByText("爱丽丝梦游仙境")).toHaveLength(2);
    // Demo cards are frontend constants outside project lists, without writes.
    expect(useProjectsStore.getState().projects).toEqual([]);
    expect(API.listProjects).toHaveBeenCalledTimes(1);
  });
});
