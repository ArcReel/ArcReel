/**
 * 逐锚点验证「引导指向的元素确实存在于界面上」。
 *
 * `RENDERERS` 用 `Record<OnboardingAnchor, …>` 标注：注册表新增锚点而这里不补渲染场景，
 * 就是编译期错误。名字漂移由类型拦，元素消失由这些用例拦 —— 两头都不靠人肉巡检。
 */

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import i18n from "@/i18n";
import { API } from "@/api";
import { GlobalHeader } from "@/components/layout/GlobalHeader";
import { OverviewCanvas } from "@/components/canvas/OverviewCanvas";
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

// 顶栏与分镜画布的重子组件与锚点无关，替身挡掉它们各自的数据依赖。
vi.mock("@/components/task-hud/TaskHud", () => ({ TaskHud: () => <div data-testid="task-hud" /> }));
vi.mock("@/components/layout/UsageDrawer", () => ({ UsageDrawer: () => null }));
vi.mock("@/components/layout/WorkspaceNotificationsDrawer", () => ({ WorkspaceNotificationsDrawer: () => null }));
vi.mock("@/components/layout/ExportScopeDialog", () => ({ ExportScopeDialog: () => null }));
vi.mock("@/components/canvas/timeline/ScriptReviewGate", () => ({ ScriptReviewGate: () => null }));
vi.mock("@/components/canvas/timeline/ShotSplitView", () => ({ ShotSplitView: () => null }));
vi.mock("@/components/canvas/timeline/EpisodeHeader", () => ({ EpisodeHeader: () => null }));

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

// 工作台四步落在演示项目上，锚点测试直接用同一份演示数据构建 props——挂载条件与
// 引导实际走到的界面一致，不另造 mock 数据。
const demoT = i18n.getFixedT("zh", "onboarding");

function renderWorkbenchHeader() {
  const { hook } = memoryLocation({ path: `/app/projects/${DEMO_PROJECT_NAME}` });
  render(
    <Router hook={hook}>
      <GlobalHeader />
    </Router>,
  );
}

/** 每个锚点挂载它所在的界面。断言统一在下面的用例里做。 */
const RENDERERS: Record<OnboardingAnchor, () => void> = {
  [ONBOARDING_ANCHORS.lobbyCreateProject]: renderLobby,
  [ONBOARDING_ANCHORS.lobbySettings]: renderLobby,
  // 演示卡只在引导跑起来时注入，所以先把 store 打开
  [ONBOARDING_ANCHORS.lobbyDemoCard]: () => {
    useOnboardingStore.setState({ active: true });
    renderLobby();
  },
  [ONBOARDING_ANCHORS.settingsProviders]: renderSettings,
  [ONBOARDING_ANCHORS.settingsAgent]: renderSettings,
  [ONBOARDING_ANCHORS.workbenchOverview]: () => {
    render(<OverviewCanvas projectName={DEMO_PROJECT_NAME} projectData={buildDemoProjectData(demoT)} readOnly />);
  },
  // 锚点挂在演示态专用的 Agent 面板上（真实面板演示态不挂载，见 anchors.ts）
  [ONBOARDING_ANCHORS.workbenchAgent]: () => {
    render(<DemoAssistantPanel />);
  },
  [ONBOARDING_ANCHORS.workbenchLorebook]: () => {
    render(
      <CharactersPage
        projectName={DEMO_PROJECT_NAME}
        characters={buildDemoProjectData(demoT).characters ?? {}}
        onSaveCharacter={vi.fn()}
        onGenerateCharacter={vi.fn()}
        onAddCharacter={vi.fn()}
        readOnly
      />,
    );
  },
  [ONBOARDING_ANCHORS.workbenchTimeline]: () => {
    render(
      <TimelineCanvas
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

  it("keeps the demo card out of the user's real project list", async () => {
    useOnboardingStore.setState({ active: true });
    renderLobby();

    // 海报与标题各出现一次
    expect(await screen.findAllByText("爱丽丝梦游仙境")).toHaveLength(2);
    // 演示卡是纯前端常量，不经过项目列表，也没有任何写入
    expect(useProjectsStore.getState().projects).toEqual([]);
    expect(API.listProjects).toHaveBeenCalledTimes(1);
  });
});
