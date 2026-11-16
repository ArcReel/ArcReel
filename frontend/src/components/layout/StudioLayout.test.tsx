import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { StudioLayout } from "@/components/layout/StudioLayout";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useCostStore } from "@/stores/cost-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";

// 会话、任务与项目事件流都走网络，外壳的开合与折叠不依赖它们。
// 真实面板自带标题栏与收起按钮，移动档的全屏层不再重复提供，桩件按同样形态给出。
vi.mock("@/components/copilot/AgentCopilot", async () => {
  const { useAppStore } = await import("@/stores/app-store");
  return {
    AgentCopilot: () => {
      const toggle = useAppStore((state) => state.toggleAssistantPanel);
      return (
        <div>
          <button type="button" aria-label="收起 Agent 面板" onClick={toggle} />
          <textarea aria-label="Agent 输入" />
        </div>
      );
    },
  };
});
vi.mock("@/hooks/useTaskRefresh", () => ({ useTaskRefresh: () => {} }));
vi.mock("@/hooks/useProjectEventsSSE", () => ({ useProjectEventsSSE: () => {} }));
// 与 GlobalHeader.test 用同一形态：同一 worker 内模块注册表可能共用，两个文件给出不同的桩
// 会让先加载的那份胜出，另一个文件就查不到自己的桩。
vi.mock("@/components/usage/UsageHeaderEntry", () => ({
  UsageHeaderEntry: ({ projectName }: { projectName: string }) => (
    <div data-testid="usage-entry" data-project={projectName} />
  ),
}));

/** 紧凑档：视口窄于 1280，`(min-width: 80rem)` 不匹配。 */
function useCompactViewport() {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

function renderShell(path: string) {
  const location = memoryLocation({ path });
  render(
    <Router hook={location.hook}>
      <StudioLayout>
        <p>画布内容</p>
      </StudioLayout>
    </Router>,
  );
  return location;
}

const agentToggle = () => screen.getByRole("button", { name: "Agent" });
const agentPanel = () => screen.getByRole("complementary", { name: "Agent 面板" });

describe("StudioLayout", () => {
  beforeEach(() => {
    localStorage.clear();
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useAppStore.setState(useAppStore.getInitialState(), true);
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);
    useTasksStore.setState(useTasksStore.getInitialState(), true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("opens and closes the agent panel from the header toggle", () => {
    useAppStore.setState({ assistantPanelOpen: true });
    renderShell("/");

    expect(agentToggle()).toHaveAttribute("aria-pressed", "true");
    expect(agentPanel()).not.toHaveAttribute("inert");

    fireEvent.click(agentToggle());

    expect(agentToggle()).toHaveAttribute("aria-pressed", "false");
    expect(agentPanel()).toHaveAttribute("inert");
  });

  it.each([
    ["running", { sessionStatus: "running" as const }, "Agent 正在运行"],
    [
      "waiting for an answer",
      { sessionStatus: "running" as const, pendingQuestion: { question_id: "q1", questions: [] } },
      "Agent 在等你回答",
    ],
  ])("describes a %s agent on the toggle while the panel is closed", (_label, assistantState, description) => {
    useAppStore.setState({ assistantPanelOpen: false });
    useAssistantStore.setState(assistantState);
    renderShell("/");

    expect(agentToggle()).toHaveAccessibleDescription(description);

    fireEvent.click(agentToggle());

    expect(agentToggle()).toHaveAccessibleDescription("");
  });

  it("collapses the sidebar on episode pages and restores the user's choice after leaving", () => {
    const { navigate } = renderShell("/characters");
    expect(screen.getByRole("button", { name: "折叠侧栏" })).toHaveAttribute("aria-expanded", "true");

    act(() => navigate("/episodes/1"));
    fireEvent.click(screen.getByRole("button", { name: "展开侧栏" }));
    expect(screen.getByRole("button", { name: "折叠侧栏" })).toHaveAttribute("aria-expanded", "true");

    // 临时展开只在当前页面有效
    act(() => navigate("/episodes/2"));
    expect(screen.getByRole("button", { name: "展开侧栏" })).toHaveAttribute("aria-expanded", "false");

    act(() => navigate("/characters"));
    expect(screen.getByRole("button", { name: "折叠侧栏" })).toHaveAttribute("aria-expanded", "true");
  });

  it("lists every episode on the icon rail even when the hidden search box still holds a query", () => {
    vi.spyOn(useCostStore.getState(), "debouncedFetch").mockImplementation(() => {});
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: {
        title: "Demo",
        content_mode: "drama",
        style: "",
        episodes: [
          { episode: 1, title: "开端", script_file: "scripts/episode_1.json" },
          { episode: 2, title: "转折", script_file: "scripts/episode_2.json" },
        ],
        characters: {},
        scenes: {},
        props: {},
      },
    });
    const { navigate } = renderShell("/characters");
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索集名或编号…" }), { target: { value: "转折" } });

    // 进入单集页自动收为图标栏：搜索框随之隐藏，图标栏不能按看不见的搜索词过滤
    act(() => navigate("/episodes/2"));

    expect(screen.getByRole("link", { name: "1 · 开端" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "2 · 转折" })).toBeInTheDocument();
  });

  it("keeps a manually collapsed sidebar collapsed outside episode pages", () => {
    const { navigate } = renderShell("/characters");
    fireEvent.click(screen.getByRole("button", { name: "折叠侧栏" }));

    act(() => navigate("/episodes"));
    act(() => navigate("/scenes"));

    expect(screen.getByRole("button", { name: "展开侧栏" })).toHaveAttribute("aria-expanded", "false");
  });

  it("collapses the sidebar to the icon rail on compact viewports", () => {
    useCompactViewport();
    renderShell("/characters");

    expect(screen.getByRole("button", { name: "展开侧栏" })).toHaveAttribute("aria-expanded", "false");
  });

  it("closes the overlay agent panel with Escape and returns focus to the toggle on compact viewports", () => {
    useCompactViewport();
    useAppStore.setState({ assistantPanelOpen: true });
    renderShell("/");
    const input = screen.getByRole("textbox", { name: "Agent 输入" });
    input.focus();

    fireEvent.keyDown(input, { key: "Escape" });

    expect(agentToggle()).toHaveAttribute("aria-pressed", "false");
    expect(agentToggle()).toHaveFocus();
  });

  it("keeps the side-by-side agent panel open on Escape on standard viewports", () => {
    useAppStore.setState({ assistantPanelOpen: true });
    renderShell("/");
    const input = screen.getByRole("textbox", { name: "Agent 输入" });
    input.focus();

    fireEvent.keyDown(input, { key: "Escape" });

    expect(agentToggle()).toHaveAttribute("aria-pressed", "true");
  });

  describe("mobile tier", () => {
    /** 移动档：`(max-width: 47.999rem)` 匹配，其余断点不匹配。 */
    function useMobileViewport() {
      vi.stubGlobal("matchMedia", (query: string) => ({
        matches: query.includes("max-width"),
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }));
    }

    it("keeps the agent panel closed on entry instead of covering the canvas", () => {
      useAppStore.setState({ assistantPanelOpen: true });
      useMobileViewport();

      renderShell("/");

      expect(agentToggle()).toHaveAttribute("aria-pressed", "false");
    });

    it("opens the workspace navigation as a drawer from the header menu button", () => {
      useMobileViewport();
      renderShell("/characters");

      // 移动档下侧栏不在文档流里，顶栏出现菜单按钮
      const open = screen.getByRole("button", { name: "打开导航菜单" });
      expect(screen.queryByRole("navigation", { name: "工作区导航" })).not.toBeInTheDocument();

      fireEvent.click(open);

      expect(screen.getByRole("dialog", { name: "工作区导航" })).toBeInTheDocument();
      expect(screen.getByRole("navigation", { name: "工作区导航" })).toBeInTheDocument();
    });

    it("closes the navigation drawer after navigating to another page", () => {
      useMobileViewport();
      const { navigate } = renderShell("/characters");
      fireEvent.click(screen.getByRole("button", { name: "打开导航菜单" }));
      expect(screen.getByRole("dialog", { name: "工作区导航" })).toBeInTheDocument();

      act(() => navigate("/scenes"));

      expect(screen.queryByRole("dialog", { name: "工作区导航" })).not.toBeInTheDocument();
    });

    it("shows the agent panel as a full-screen layer with its own close button", () => {
      useMobileViewport();
      renderShell("/");

      fireEvent.click(agentToggle());

      const close = screen.getByRole("button", { name: "收起 Agent 面板" });
      expect(close).toBeInTheDocument();
      expect(screen.getByRole("textbox", { name: "Agent 输入" })).toBeInTheDocument();

      fireEvent.click(close);
      expect(screen.queryByRole("textbox", { name: "Agent 输入" })).not.toBeInTheDocument();
    });
  });
});
