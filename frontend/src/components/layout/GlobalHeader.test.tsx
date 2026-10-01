import { render, screen, waitFor } from "@testing-library/react";
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

vi.mock("./WorkspaceNotificationsDrawer", () => ({
  WorkspaceNotificationsDrawer: ({ open }: { open: boolean }) =>
    open ? <div data-testid="notifications-drawer" /> : null,
}));

/** 打开「导出项目」弹窗并选择归档范围。 */
async function openExportScope(option: "current" | "full") {
  screen.getByRole("button", { name: "导出项目归档" }).click();
  const name = option === "current" ? /仅当前版本/ : /全部数据/;
  (await screen.findByRole("button", { name })).click();
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
    // 集 ID 与播出顺序不同：ID 3 排第一，ID 1 排第二且没有标题
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

  it("shows unread notification count and opens the drawer", async () => {
    useAppStore.getState().pushWorkspaceNotification({
      text: "AI 刚更新了道具「玉佩」，点击查看",
      target: {
        type: "prop",
        id: "玉佩",
        route: "/props",
      },
    });

    renderHeader();

    expect(screen.getByTitle("会话通知: 1 条")).toBeInTheDocument();
    screen.getByRole("button", { name: "打开通知中心" }).click();
    expect(await screen.findByTestId("notifications-drawer")).toBeInTheDocument();
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
  });

  it("「导出项目」只剩项目归档，并提示成片与剪映草稿在剪辑视图导出", async () => {
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: PROJECT_WITH_EPISODES as never });

    renderHeader();
    screen.getByRole("button", { name: "导出项目归档" }).click();

    expect(await screen.findByRole("button", { name: /仅当前版本/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /全部数据/ })).toBeInTheDocument();
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

    // 浏览器前进/后退等场景会复用同一个 GlobalHeader 实例切到演示项目——已打开的
    // 导出弹窗须随之关闭，不能继续展示可点击的导出操作
    useProjectsStore.setState({ currentProjectName: DEMO_PROJECT_NAME });

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

  it("renders asset library button", async () => {
    renderHeader();

    expect(screen.getByRole("button", { name: "资产库" })).toBeInTheDocument();
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

    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toContain("导出失败");
    });
  });
});
