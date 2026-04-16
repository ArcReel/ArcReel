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
import { useUsageStore } from "@/stores/usage-store";

vi.mock("@/components/task-hud/TaskHud", () => ({
  TaskHud: () => <div data-testid="task-hud" />,
}));

vi.mock("./UsageDrawer", () => ({
  UsageDrawer: () => <div data-testid="usage-drawer" />,
}));

vi.mock("./WorkspaceNotificationsDrawer", () => ({
  WorkspaceNotificationsDrawer: ({ open }: { open: boolean }) =>
    open ? <div data-testid="notifications-drawer" /> : null,
}));

vi.mock("./ExportScopeDialog", () => ({
  ExportScopeDialog: ({
    open,
    onSelect,
  }: {
    open: boolean;
    onClose: () => void;
    onSelect: (scope: "current" | "full") => void;
    anchorRef: React.RefObject<HTMLElement | null>;
    episodes?: unknown[];
    onJianyingExport?: (episode: number, draftPath: string, jianyingVersion: string) => void;
    jianyingExporting?: boolean;
  }) =>
    open ? (
      <div data-testid="export-scope-dialog">
        <button data-testid="scope-current" onClick={() => onSelect("current")}>
          仅当前版本
        </button>
        <button data-testid="scope-full" onClick={() => onSelect("full")}>
          全部数据
        </button>
      </div>
    ) : null,
}));

function renderHeader() {
  const { hook } = memoryLocation({ path: "/characters" });
  return render(
    <Router hook={hook}>
      <GlobalHeader />
    </Router>,
  );
}

describe("GlobalHeader", () => {
  beforeEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useAppStore.setState(useAppStore.getInitialState(), true);
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    useUsageStore.setState(useUsageStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("prefers the project title over the internal project name", async () => {
    vi.spyOn(API, "getUsageStats").mockResolvedValue({
      total_cost: 0,
      image_count: 0,
      video_count: 0,
      failed_count: 0,
      total_count: 0,
    });

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

    await waitFor(() => {
      expect(API.getUsageStats).toHaveBeenCalledWith({
        projectName: "halou-92d19a04",
      });
    });
  });

  it("shows unread notification count and opens the drawer", async () => {
    vi.spyOn(API, "getUsageStats").mockResolvedValue({
      total_cost: 0,
      image_count: 0,
      video_count: 0,
      failed_count: 0,
      total_count: 0,
    });

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
    vi.spyOn(API, "getUsageStats").mockResolvedValue({
      total_cost: 0,
      image_count: 0,
      video_count: 0,
      failed_count: 0,
      total_count: 0,
    });
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
    // Click export button to open dialog
    screen.getByRole("button", { name: "导出当前项目 ZIP" }).click();

    // Wait for dialog to appear then click "仅当前版本"
    const scopeBtn = await screen.findByTestId("scope-current");
    scopeBtn.click();

    await waitFor(() => {
      expect(API.requestExportToken).toHaveBeenCalledWith("demo", "current");
    });
    expect(anchorClick).toHaveBeenCalled();
    expect(useAppStore.getState().toast?.text).toContain("包含 1 条诊断");
  });

  it("shows an error toast when exporting fails", async () => {
    vi.spyOn(API, "getUsageStats").mockResolvedValue({
      total_cost: 0,
      image_count: 0,
      video_count: 0,
      failed_count: 0,
      total_count: 0,
    });
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
    screen.getByRole("button", { name: "导出当前项目 ZIP" }).click();

    const scopeBtn = await screen.findByTestId("scope-full");
    scopeBtn.click();

    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toContain("导出失败");
    });
  });
});
