import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { StudioLayout } from "@/components/layout/StudioLayout";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";

// 会话、任务与项目事件流都走网络，外壳的开合与折叠不依赖它们
vi.mock("@/components/copilot/AgentCopilot", () => ({
  AgentCopilot: () => <textarea aria-label="Agent 输入" />,
}));
vi.mock("@/hooks/useTaskRefresh", () => ({ useTaskRefresh: () => {} }));
vi.mock("@/hooks/useProjectEventsSSE", () => ({ useProjectEventsSSE: () => {} }));
vi.mock("@/components/usage/UsageHeaderEntry", () => ({ UsageHeaderEntry: () => null }));

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
});
