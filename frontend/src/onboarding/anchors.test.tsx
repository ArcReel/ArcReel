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
import { API } from "@/api";
import { ProjectsPage } from "@/components/pages/ProjectsPage";
import { useAppStore } from "@/stores/app-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useProjectsStore } from "@/stores/projects-store";
import { ONBOARDING_ANCHORS, type OnboardingAnchor } from "./anchors";
import { anchorSelector } from "./tour";

vi.mock("@/components/pages/CreateProjectModal", () => ({
  CreateProjectModal: () => <div data-testid="create-project-modal" />,
}));

function renderLobby() {
  const { hook } = memoryLocation({ path: "/app/projects" });
  render(
    <Router hook={hook}>
      <ProjectsPage />
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
};

describe("onboarding anchors", () => {
  beforeEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useAppStore.setState(useAppStore.getInitialState(), true);
    useOnboardingStore.setState(useOnboardingStore.getInitialState(), true);
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
