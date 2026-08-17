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
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";

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
    onJianyingExport,
  }: {
    open: boolean;
    onClose: () => void;
    onSelect: (scope: "current" | "full") => void;
    anchorRef: React.RefObject<HTMLElement | null>;
    episodes?: unknown[];
    onJianyingExport?: (
      episode: number,
      draftPath: string,
      jianyingVersion: string,
      narrationDelivery: "post_production" | "use_tts",
    ) => void;
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
        {onJianyingExport && (
          <button
            data-testid="scope-jianying"
            onClick={() => onJianyingExport(1, "/drafts", "6", "post_production")}
          >
            剪映草稿
          </button>
        )}
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
      expect(API.getUsageStats).toHaveBeenCalledWith(
        { projectName: "halou-92d19a04" },
        expect.anything(),
      );
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

  it("ad 参考路线导出不做旧签名预检", async () => {
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
      diagnostics: { blocking: [], auto_fixed: [], warnings: [] },
    });
    const listUnits = vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [
        {
          unit_id: "E1U1",
          shot_ids: ["E1S1"],
          references: [],
          generated_assets: { video_clip: "reference_videos/E1U1.mp4", status: "completed" },
          stale: true,
        },
        {
          unit_id: "E1U2",
          shot_ids: ["E1S2"],
          references: [],
          generated_assets: { video_clip: "reference_videos/E1U2.mp4", status: "completed" },
        },
      ],
    } as never);
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    useProjectsStore.setState({
      currentProjectName: "ad-demo",
      currentProjectData: {
        title: "带货短片",
        content_mode: "ad",
        generation_mode: "reference_video",
        style: "明亮写实",
        episodes: [],
        characters: {},
        scenes: {},
        props: {},
      },
    });

    renderHeader();
    screen.getByRole("button", { name: "导出当前项目 ZIP" }).click();
    (await screen.findByTestId("scope-jianying")).click();

    await waitFor(() => {
      expect(anchorClick).toHaveBeenCalled();
    });
    expect(listUnits).not.toHaveBeenCalled();
    expect(
      useAppStore
        .getState()
        .workspaceNotifications.some((n) => n.text.includes("剧本已变更")),
    ).toBe(false);
  });

  it("ad 参考路线导出不受 unit 查询故障影响", async () => {
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
      diagnostics: { blocking: [], auto_fixed: [], warnings: [] },
    });
    vi.spyOn(API, "listReferenceVideoUnits").mockRejectedValue(new Error("boom"));
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    useProjectsStore.setState({
      currentProjectName: "ad-demo",
      currentProjectData: {
        title: "带货短片",
        content_mode: "ad",
        generation_mode: "reference_video",
        style: "明亮写实",
        episodes: [],
        characters: {},
        scenes: {},
        props: {},
      },
    });

    renderHeader();
    screen.getByRole("button", { name: "导出当前项目 ZIP" }).click();
    (await screen.findByTestId("scope-jianying")).click();

    await waitFor(() => {
      expect(API.requestExportToken).toHaveBeenCalledWith("ad-demo", "current");
    });
    expect(anchorClick).toHaveBeenCalled();
  });

  it("ad 分镜路线项目导出剪映草稿不做 stale 预检", async () => {
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
      diagnostics: { blocking: [], auto_fixed: [], warnings: [] },
    });
    const listUnits = vi.spyOn(API, "listReferenceVideoUnits");
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    useProjectsStore.setState({
      currentProjectName: "ad-demo",
      currentProjectData: {
        title: "带货短片",
        content_mode: "ad",
        generation_mode: "storyboard",
        style: "明亮写实",
        episodes: [],
        characters: {},
        scenes: {},
        props: {},
      },
    });

    renderHeader();
    screen.getByRole("button", { name: "导出当前项目 ZIP" }).click();
    (await screen.findByTestId("scope-jianying")).click();

    await waitFor(() => {
      expect(anchorClick).toHaveBeenCalled();
    });
    expect(listUnits).not.toHaveBeenCalled();
  });

  it("非 ad 项目导出剪映草稿不做 stale 预检", async () => {
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
      diagnostics: { blocking: [], auto_fixed: [], warnings: [] },
    });
    const listUnits = vi.spyOn(API, "listReferenceVideoUnits");
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: {
        title: "说书项目",
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
    (await screen.findByTestId("scope-jianying")).click();

    await waitFor(() => {
      expect(anchorClick).toHaveBeenCalled();
    });
    expect(listUnits).not.toHaveBeenCalled();
  });

  it("closes an already-open export dialog when the workbench switches to the demo project", async () => {
    vi.spyOn(API, "getUsageStats").mockResolvedValue({
      total_cost: 0,
      image_count: 0,
      video_count: 0,
      failed_count: 0,
      total_count: 0,
    });

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
    screen.getByRole("button", { name: "导出当前项目 ZIP" }).click();
    expect(await screen.findByTestId("export-scope-dialog")).toBeInTheDocument();

    // 浏览器前进/后退等场景会复用同一个 GlobalHeader 实例切到演示项目——已打开的
    // 导出弹窗须随之关闭，不能继续展示可点击的导出/剪映草稿操作
    useProjectsStore.setState({ currentProjectName: DEMO_PROJECT_NAME });

    await waitFor(() => {
      expect(screen.queryByTestId("export-scope-dialog")).not.toBeInTheDocument();
    });
  });

  it("discards a stale usage-stats response after the project switches before it resolves", async () => {
    const pending: {
      signal: AbortSignal | undefined;
      resolve: (v: Record<string, unknown>) => void;
    }[] = [];
    vi.spyOn(API, "getUsageStats").mockImplementation(
      (_filters, options) =>
        new Promise((resolve, reject) => {
          options?.signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
          pending.push({ signal: options?.signal, resolve });
        }),
    );

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
    await waitFor(() => expect(pending.length).toBe(1));

    // 请求未返回前切到演示项目——effect 依赖变化触发 cleanup，abort 前一份请求
    useProjectsStore.setState({ currentProjectName: DEMO_PROJECT_NAME });
    await waitFor(() => expect(pending.length).toBe(2));
    expect(pending[0].signal?.aborted).toBe(true);

    pending[1].resolve({ cost_by_currency: { usd: 1 } });

    await waitFor(() => {
      expect(useUsageStore.getState().stats?.cost_by_currency).toEqual({ usd: 1 });
    });
  });

  it("renders asset library button", async () => {
    vi.spyOn(API, "getUsageStats").mockResolvedValue({
      total_cost: 0,
      image_count: 0,
      video_count: 0,
      failed_count: 0,
      total_count: 0,
    });

    renderHeader();

    expect(screen.getByRole("button", { name: "资产库" })).toBeInTheDocument();
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
