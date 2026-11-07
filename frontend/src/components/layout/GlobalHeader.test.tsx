import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { GlobalHeader } from "@/components/layout/GlobalHeader";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";

vi.mock("@/components/usage/UsageHeaderEntry", () => ({
  UsageHeaderEntry: ({ projectName }: { projectName: string }) => (
    <div data-testid="usage-entry" data-project={projectName} />
  ),
}));

/** Open Export project and choose archive scopes. */
async function openExportScope(option: "current" | "full") {
  screen.getByRole("button", { name: "导出项目归档" }).click();
  const name = option === "current" ? /仅当前版本/ : /全部数据/;
  const dialog = await screen.findByRole("dialog", { name: "选择导出范围" });
  fireEvent.click(within(dialog).getByRole("radio", { name }));
  fireEvent.click(within(dialog).getByRole("button", { name: "导出" }));
}

function renderHeader(path = "/characters") {
  const location = memoryLocation({ path, record: true });
  render(
    <Router hook={location.hook}>
      <GlobalHeader />
    </Router>,
  );
  return location;
}

const PROJECT_WITH_EPISODES = {
  title: "旁白项目",
  content_mode: "narration",
  style: "Anime",
  episodes: [
    // IDs differ from broadcast order: 3 first, 1 second without titles.
    { episode: 3, title: "开端", script_file: "scripts/episode_3.json" },
    { episode: 1, title: "", script_file: "scripts/episode_1.json" },
  ],
  characters: {},
  scenes: {},
  props: {},
} as const;

describe("GlobalHeader", () => {
  beforeEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useAppStore.setState(useAppStore.getInitialState(), true);
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("prefers the project title over the internal project name", async () => {
    useProjectsStore.setState({
      currentProjectName: "halou-92d19a04",
      currentProjectData: {
        title: "哈喽项目",
        content_mode: "narration",
        style: "Anime",
        episodes: [],
        characters: {},
        scenes: {},
        props: {},
      },
    });

    renderHeader();

    expect(screen.getByText("哈喽项目")).toBeInTheDocument();
    expect(screen.queryByText("halou-92d19a04")).not.toBeInTheDocument();
  });

  it("exports the current project zip via browser-native download", async () => {
    vi.spyOn(API, "requestExportToken").mockResolvedValue({
      download_token: "test-download-token",
      expires_in: 300,
      diagnostics: {
        blocking: [],
        auto_fixed: [{ code: "current_asset_restored_from_version", message: "修复视频引用" }],
        warnings: [],
      },
    });
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: {
        title: "导出项目",
        content_mode: "narration",
        style: "Anime",
        episodes: [],
        characters: {},
        scenes: {},
        props: {},
      },
    });

    renderHeader();
    await openExportScope("current");

    await waitFor(() => {
      expect(API.requestExportToken).toHaveBeenCalledWith("demo", "current");
    });
    expect(anchorClick).toHaveBeenCalled();
    expect(useAppStore.getState().toast?.text).toContain("包含 1 条诊断");
    expect(await screen.findByRole("dialog", { name: "导出诊断" })).toBeInTheDocument();
  });

  it("「导出项目」只剩项目归档，并提示成片与剪映草稿在剪辑视图导出", async () => {
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: PROJECT_WITH_EPISODES as never });

    renderHeader();
    screen.getByRole("button", { name: "导出项目归档" }).click();

    expect(await screen.findByRole("radio", { name: /仅当前版本/ })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /全部数据/ })).toBeInTheDocument();
    expect(screen.queryByText(/剪映草稿目录/)).not.toBeInTheDocument();
    expect(screen.getByText(/成片与剪映草稿在各集的剪辑视图中导出/)).toBeInTheDocument();
  });

  it("在集页时，提示里的链接跳到当前集的剪辑视图", async () => {
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: PROJECT_WITH_EPISODES as never });

    const location = renderHeader("/episodes/1");
    screen.getByRole("button", { name: "导出项目归档" }).click();
    (await screen.findByRole("button", { name: "打开「第 2 集」的剪辑视图" })).click();

    await waitFor(() => {
      expect(location.history?.at(-1)).toBe("/app/projects/demo/episodes/1?view=edit");
    });
  });

  it("不在集页时，提示里的链接跳到第一集的剪辑视图", async () => {
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: PROJECT_WITH_EPISODES as never });

    const location = renderHeader();
    screen.getByRole("button", { name: "导出项目归档" }).click();
    (await screen.findByRole("button", { name: "打开「开端」的剪辑视图" })).click();

    await waitFor(() => {
      expect(location.history?.at(-1)).toBe("/app/projects/demo/episodes/3?view=edit");
    });
  });

  it("closes an already-open export dialog when the workbench switches to the demo project", async () => {
    useProjectsStore.setState({
      currentProjectName: "real-project",
      currentProjectData: {
        title: "真实项目",
        content_mode: "narration",
        style: "Anime",
        episodes: [],
        characters: {},
        scenes: {},
        props: {},
      },
    });

    renderHeader();
    screen.getByRole("button", { name: "导出项目归档" }).click();
    expect(await screen.findByText("选择导出范围")).toBeInTheDocument();

    // Browser Forward/Back may reuse GlobalHeader into demos; open exports
    // must close, never retaining clickable writes.
    useProjectsStore.setState({ currentProjectName: DEMO_PROJECT_NAME });

    await waitFor(() => {
      expect(screen.queryByText("选择导出范围")).not.toBeInTheDocument();
    });
  });

  it("closes an already-open export dialog when the workbench switches to another real project", async () => {
    useProjectsStore.setState({ currentProjectName: "project-a", currentProjectData: PROJECT_WITH_EPISODES as never });

    renderHeader();
    screen.getByRole("button", { name: "导出项目归档" }).click();
    expect(await screen.findByText("选择导出范围")).toBeInTheDocument();

    // Dialogs captured project A; reused headers navigating B through browser history
    // must close them, preventing later scope selection exporting A.
    useProjectsStore.setState({ currentProjectName: "project-b", currentProjectData: PROJECT_WITH_EPISODES as never });

    await waitFor(() => {
      expect(screen.queryByText("选择导出范围")).not.toBeInTheDocument();
    });
  });

  it("renders the usage entry for the current project", () => {
    useProjectsStore.setState({ currentProjectName: "real-project" });

    renderHeader();

    expect(screen.getByTestId("usage-entry")).toHaveAttribute("data-project", "real-project");
  });

  it("hides the usage entry and closes an open popover in the demo project", () => {
    useAppStore.setState({ usagePanelOpen: true });
    useProjectsStore.setState({ currentProjectName: DEMO_PROJECT_NAME });

    renderHeader();

    expect(screen.queryByTestId("usage-entry")).not.toBeInTheDocument();
    expect(useAppStore.getState().usagePanelOpen).toBe(false);
  });

  it("marks the demo project as read-only next to its title, with the explanation on focus", async () => {
    useProjectsStore.setState({ currentProjectName: DEMO_PROJECT_NAME });
    renderHeader();

    const hint = "你正在查看一个示例项目。编辑、生成、上传和导出功能在演示中不可用。";
    const badge = screen.getByText("演示 · 只读");
    // Badges carry explanations directly for screen readers, focusable with identical focus hints.
    expect(badge).toHaveTextContent(hint);
    act(() => badge.focus());
    expect(badge).toHaveFocus();
    await waitFor(() => expect(screen.getAllByText(hint)).toHaveLength(2));
  });

  it("shows no read-only badge on a real project", () => {
    useProjectsStore.setState({ currentProjectName: "real-project" });
    renderHeader();

    expect(screen.queryByText("演示 · 只读")).not.toBeInTheDocument();
  });

  // Project settings live in switchers; gears always open globals, even in projects.
  it("links the gear to global settings and the back link to the lobby", () => {
    useProjectsStore.setState({ currentProjectName: "real-project" });

    renderHeader();

    expect(screen.getByRole("link", { name: "全局设置" })).toHaveAttribute("href", "/app/settings");
    expect(screen.getByRole("link", { name: "项目" })).toHaveAttribute("href", "/app/projects");
  });

  it("shows an error toast when exporting fails", async () => {
    vi.spyOn(API, "requestExportToken").mockRejectedValue(new Error("network"));

    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: {
        title: "导出项目",
        content_mode: "narration",
        style: "Anime",
        episodes: [],
        characters: {},
        scenes: {},
        props: {},
      },
    });

    renderHeader();
    await openExportScope("full");

    expect(API.requestExportToken).toHaveBeenCalledWith("demo", "full");
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toContain("导出失败");
    });
    // Export failures persist workspace notifications for later review.
    expect(useAppStore.getState().workspaceNotifications[0]?.text).toContain("导出失败");
  });
});
