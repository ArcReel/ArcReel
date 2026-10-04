import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { API } from "@/api";
import { ApiRequestError } from "@/api/errors";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { useAppStore } from "@/stores/app-store";
import { useCostStore } from "@/stores/cost-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { CostEstimateResponse, ProjectData } from "@/types";

import { OverviewCanvas } from "./OverviewCanvas";

vi.mock("@/components/canvas/AdInitCanvas", () => ({
  AdInitCanvas: () => <div data-testid="ad-init-canvas">ad-init</div>,
}));

function makeProjectData(overrides: Partial<ProjectData> = {}): ProjectData {
  return {
    title: "Demo",
    content_mode: "drama",
    style: "Anime",
    aspect_ratio: "9:16",
    overview: {
      synopsis: "summary",
      genre: "fantasy",
      theme: "growth",
      world_setting: "palace",
    },
    episodes: [
      { episode: 1, title: "EP1", script_file: "scripts/episode_1.json", duration_seconds: 50 },
      { episode: 2, title: "EP2", script_file: "scripts/episode_2.json", duration_seconds: 40 },
    ],
    characters: {},
    scenes: {},
    props: {},
    ...overrides,
  };
}

const EMPTY_PROJECT: Partial<ProjectData> = { overview: undefined, episodes: [], whole_source_files: [] };

function withRouter(children: ReactNode, location = memoryLocation({ path: "/", record: true })) {
  return (
    <Router hook={location.hook}>
      <LeaveGuardProvider>{children}</LeaveGuardProvider>
    </Router>
  );
}

function renderOverview(props: Partial<Parameters<typeof OverviewCanvas>[0]> = {}) {
  const location = memoryLocation({ path: "/", record: true });
  const element = (next: Partial<Parameters<typeof OverviewCanvas>[0]>) =>
    withRouter(<OverviewCanvas projectName="demo" projectData={makeProjectData()} {...next} />, location);
  const view = render(element(props));
  return { ...view, location, rerender: (next: Partial<Parameters<typeof OverviewCanvas>[0]>) => view.rerender(element(next)) };
}

function setCost(
  projectName: string,
  actual: Record<string, Record<string, number>>,
  overrides: Partial<CostEstimateResponse> = {},
) {
  useCostStore.setState({
    costData: {
      project_name: projectName,
      models: { image: { provider: "p", model: "m" }, video: { provider: "p", model: "m" } },
      episodes: [],
      project_totals: { estimate: { image: { USD: 2 }, video: { USD: 3 } }, actual },
      unpriced: { estimate: [], actual: [] },
      missing_local_calls: false,
      ...overrides,
    },
  });
}

describe("OverviewCanvas", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useCostStore.setState(useCostStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");
  });

  it("summarizes mode, aspect ratio, episode count and script length in the header", () => {
    renderOverview();

    expect(screen.getByRole("heading", { level: 1, name: "Demo" })).toBeInTheDocument();
    expect(screen.getByText("剧情演绎 · 竖屏 9:16 · 2 集 · 脚本时长 1:30")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "项目设置" })).toHaveAttribute("href", "/app/projects/demo/settings");
  });

  it("links each asset kind to its gallery and appends the outdated count", () => {
    renderOverview({
      projectData: makeProjectData({
        status: {
          needs_repair: false,
          repair_reason: null,
          source_remaining: false,
          episodes_summary: {} as never,
          assets: {
            character: { total: 11, available: 11, stale: 2 },
            scene: { total: 16, available: 9, stale: 0 },
            prop: { total: 13, available: 13, stale: 0 },
            product: { total: 1, available: 1, stale: 0 },
          },
        },
      }),
    });

    expect(screen.getByRole("link", { name: "角色 11/11 · 2 过期" })).toHaveAttribute("href", "/characters");
    expect(screen.getByRole("link", { name: "场景 9/16" })).toHaveAttribute("href", "/scenes");
    expect(screen.getByRole("link", { name: "道具 13/13" })).toHaveAttribute("href", "/props");
    // 商品只属于广告项目
    expect(screen.queryByRole("link", { name: /商品/ })).not.toBeInTheDocument();
  });

  it("shows estimate and spend on one line and breaks them down in the details popover", async () => {
    const user = userEvent.setup();
    setCost("demo", { video: { USD: 1 }, unassigned: { USD: 1.25 } });
    renderOverview();

    expect(screen.getByText("预估 $5.00")).toBeInTheDocument();
    expect(screen.getByText("已花 $2.25")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "明细" }));
    const popover = await screen.findByRole("dialog", { name: "费用明细" });
    expect(within(popover).getByText("历史支出（未归属当前脚本）")).toBeInTheDocument();
    expect(within(popover).getByRole("link", { name: "查看使用记录" })).toHaveAttribute(
      "href",
      "/app/settings?section=usage&u_project=demo",
    );
  });

  it("shows a dash instead of another project's cost left in the store", () => {
    setCost("another-project", { video: { USD: 9 } });
    renderOverview();

    expect(screen.getByText("预估 —")).toBeInTheDocument();
    expect(screen.getByText("已花 —")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "明细" })).not.toBeInTheDocument();
  });

  it("shows a real zero spend as 0 but an unrecorded spend as a dash with the reason", () => {
    setCost("demo", {});
    const view = renderOverview();
    expect(screen.getByText("已花 0")).toBeInTheDocument();

    setCost("demo", {}, { missing_local_calls: true });
    view.rerender({});
    expect(screen.getByText("已花 —")).toBeInTheDocument();
    expect(screen.getByText(/本机没有这个项目的调用记录/)).toBeInTheDocument();
  });

  it("explains unpriced estimates and calls and links each custom model to its price", () => {
    const relayImage = { call_type: "image", provider: "custom-3", provider_name: "Relay", model: "img" } as const;
    setCost(
      "demo",
      { video: { USD: 1 } },
      {
        project_totals: { estimate: {}, actual: { video: { USD: 1 } } },
        unpriced: { estimate: [{ ...relayImage, count: 2 }], actual: [{ ...relayImage, count: 1 }] },
      },
    );
    renderOverview();

    // 全部预估都没有价格时写「—」；部分调用没有价格时仍显示已计价的金额
    expect(screen.getByText("预估 —")).toBeInTheDocument();
    expect(screen.getByText("已花 $1.00")).toBeInTheDocument();
    expect(screen.getByText("有 2 项预估的模型没有价格，未计入预估。")).toBeInTheDocument();
    expect(screen.getByText("有 1 次调用的模型没有价格，未计入已花。")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Relay / img" })).toHaveAttribute(
      "href",
      "/app/settings?section=providers&custom=3&model=img",
    );
  });

  it("does not fetch cost on a read-only project and cancels a real project's queued request", async () => {
    vi.useFakeTimers();
    const getCostEstimate = vi.spyOn(API, "getCostEstimate");
    try {
      const view = renderOverview({ projectName: "real-project" });
      view.rerender({ projectName: "onboarding_demo", readOnly: true });
      await vi.advanceTimersByTimeAsync(600);

      expect(getCostEstimate).not.toHaveBeenCalled();
      expect(useCostStore.getState().costData).toBeNull();
      expect(screen.getByText(/演示项目不在本机生成/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("saves the four story fields together, trimmed", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(API, "updateOverview").mockResolvedValue({ success: true });
    renderOverview();

    const genre = screen.getByRole("textbox", { name: "类型" });
    await user.clear(genre);
    await user.type(genre, "  悬疑 ");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("demo", {
        synopsis: "summary",
        genre: "悬疑",
        theme: "growth",
        world_setting: "palace",
      }),
    );
  });

  it("intercepts leaving the overview while the story setting has unsaved changes", async () => {
    const user = userEvent.setup();
    const { location } = renderOverview({
      projectData: makeProjectData({
        status: {
          needs_repair: false,
          repair_reason: null,
          source_remaining: false,
          episodes_summary: {} as never,
          assets: { character: { total: 1, available: 1, stale: 0 } },
        },
      }),
    });

    await user.type(screen.getByRole("textbox", { name: "梗概" }), " more");
    await user.click(screen.getByRole("link", { name: /角色/ }));

    expect(await screen.findByRole("alertdialog", { name: "有未保存的修改" })).toBeInTheDocument();
    expect(location.history.at(-1)).toBe("/");
  });

  it("asks before regenerating and names the unsaved changes that will be discarded", async () => {
    const user = userEvent.setup();
    const generate = vi.spyOn(API, "generateOverview").mockReturnValue(new Promise(() => {}));
    renderOverview({ projectData: makeProjectData({ whole_source_files: [{ source_file: "source/novel.txt" }] }) });

    const synopsis = screen.getByRole("textbox", { name: "梗概" });
    await user.type(synopsis, " draft");
    await user.click(screen.getByRole("button", { name: "放弃修改并重新生成" }));

    const dialog = await screen.findByRole("alertdialog", { name: "从原文重新生成故事设定？" });
    expect(within(dialog).getByText(/尚未保存的修改也会丢失/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(generate).not.toHaveBeenCalled();
    expect(synopsis).toHaveValue("summary draft");

    await user.click(screen.getByRole("button", { name: "放弃修改并重新生成" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "放弃修改并重新生成" }),
    );

    expect(generate).toHaveBeenCalledWith("demo", expect.anything());
    expect(screen.getByRole("status")).toHaveTextContent("正在读取原文…");
  });

  it("generates from the source without asking when the story setting is still empty", async () => {
    const user = userEvent.setup();
    const generate = vi.spyOn(API, "generateOverview").mockResolvedValue({ success: true, overview: {} as never });
    renderOverview({
      projectData: makeProjectData({ ...EMPTY_PROJECT, whole_source_files: [{ source_file: "source/novel.txt" }] }),
    });

    // 有原文但还没有故事设定时不是空项目：直接显示概览，可以手写，也可以从原文生成
    expect(screen.getByRole("textbox", { name: "梗概" })).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "从原文生成" }));

    expect(generate).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("shows the way out when generating fails because the model output was truncated", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "generateOverview").mockRejectedValue(
      new ApiRequestError(
        "truncated",
        { code: "text_output_truncated", params: { provider_id: "custom-3", model: "my-llm", custom_model: true } },
        422,
      ),
    );
    renderOverview({
      projectData: makeProjectData({ ...EMPTY_PROJECT, whole_source_files: [{ source_file: "source/novel.txt" }] }),
    });

    await user.click(screen.getByRole("button", { name: "从原文生成" }));

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("button", { name: "去登记最大输出长度" })).toBeInTheDocument();
    // 失败后字段回来，可以手写或再试一次
    expect(screen.getByRole("textbox", { name: "梗概" })).toBeInTheDocument();
  });

  it("shows the read-only story setting as text without edit or generate entries", () => {
    renderOverview({ projectName: "onboarding_demo", readOnly: true });

    expect(screen.getByText("summary")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /生成/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "项目设置" })).not.toBeInTheDocument();
  });

  describe("empty project", () => {
    it("welcomes with the project title and opens the upload dialog with dropped files", () => {
      renderOverview({ projectData: makeProjectData({ ...EMPTY_PROJECT, title: "哈喽项目" }) });

      expect(screen.getByRole("heading", { name: "开始制作《哈喽项目》" })).toBeInTheDocument();
      fireEvent.drop(screen.getByRole("button", { name: /拖入原文/ }), {
        dataTransfer: { files: [new File(["x"], "novel.txt", { type: "text/plain" })] },
      });

      expect(screen.getByRole("dialog", { name: "上传原文" })).toBeInTheDocument();
      expect(screen.getByTitle("novel.txt")).toBeInTheDocument();
    });

    it("switches to the overview right after the first whole-source upload and fills the story setting in place", async () => {
      const user = userEvent.setup();
      vi.spyOn(API, "uploadFile").mockResolvedValue({ success: true, path: "source/novel.txt", filename: "novel.txt" });
      let finishGenerate!: () => void;
      vi.spyOn(API, "generateOverview").mockImplementation(
        () =>
          new Promise((resolve) => {
            finishGenerate = () => resolve({ success: true, overview: {} as never });
          }),
      );
      const view = renderOverview({ projectData: makeProjectData(EMPTY_PROJECT) });

      fireEvent.drop(screen.getByRole("button", { name: /拖入原文/ }), {
        dataTransfer: { files: [new File(["x"], "novel.txt", { type: "text/plain" })] },
      });
      await user.click(screen.getByRole("button", { name: "上传 1 个文件" }));

      expect(await screen.findByText("正在读取原文…")).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: /开始制作/ })).not.toBeInTheDocument();
      expect(screen.getByRole("heading", { level: 1, name: "Demo" })).toBeInTheDocument();

      // 生成完成，项目刷新带回故事设定
      await act(async () => finishGenerate());
      view.rerender({
        projectData: makeProjectData({ episodes: [], whole_source_files: [{ source_file: "source/novel.txt" }] }),
      });

      await waitFor(() => expect(screen.queryByText("正在读取原文…")).not.toBeInTheDocument());
      expect(screen.getByRole("textbox", { name: "梗概" })).toHaveValue("summary");
    });

    it("does not generate when only per-episode sources were added", async () => {
      const user = userEvent.setup();
      vi.spyOn(API, "uploadFile").mockResolvedValue({ success: true, path: "source/second.txt", filename: "second.txt" });
      const generate = vi.spyOn(API, "generateOverview");
      renderOverview({ projectData: makeProjectData(EMPTY_PROJECT) });

      await user.click(screen.getByRole("button", { name: /拖入原文/ }));
      const dialog = await screen.findByRole("dialog", { name: "上传原文" });
      await user.click(within(dialog).getByRole("radio", { name: /逐集/ }));
      fireEvent.change(within(dialog).getByLabelText("选择文件"), {
        target: { files: [new File(["x"], "second.txt")] },
      });
      await user.click(within(dialog).getByRole("button", { name: "添加为第 1 集" }));

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(generate).not.toHaveBeenCalled();
    });
  });

  describe("agent handoff hint", () => {
    it("opens the Agent panel once when the story setting goes from empty to filled", () => {
      useAppStore.setState({ assistantPanelOpen: false });
      // 提示按「项目:trigger」记入 sessionStorage 去重，用本条用例独有的项目名
      const view = renderOverview({
        projectName: "handoff-fill",
        projectData: makeProjectData({ overview: undefined, whole_source_files: [{ source_file: "source/a.txt" }] }),
      });
      view.rerender({ projectName: "handoff-fill", projectData: makeProjectData() });

      expect(useAppStore.getState().assistantPanelOpen).toBe(true);
    });

    it("does not fire when switching from an empty project to another project that already has a story setting", () => {
      useAppStore.setState({ assistantPanelOpen: false });
      const view = renderOverview({ projectName: "project-a", projectData: makeProjectData(EMPTY_PROJECT) });
      view.rerender({ projectName: "project-b", projectData: makeProjectData() });

      expect(useAppStore.getState().assistantPanelOpen).toBe(false);
    });

    it("does not fire or stay visible on a read-only project", () => {
      useAppStore.setState({ assistantPanelOpen: false });
      const view = renderOverview({ projectName: "real", projectData: makeProjectData(EMPTY_PROJECT) });
      view.rerender({ projectName: "real", projectData: makeProjectData() });
      expect(screen.getByText("准备就绪")).toBeInTheDocument();

      useAppStore.setState({ assistantPanelOpen: false });
      view.rerender({ projectName: "onboarding_demo", projectData: makeProjectData(), readOnly: true });
      expect(screen.queryByText("准备就绪")).not.toBeInTheDocument();

      // 途经只读演示项目再进入另一个真实项目，不会重放上一个项目的提示
      view.rerender({ projectName: "project-b", projectData: makeProjectData() });
      expect(useAppStore.getState().assistantPanelOpen).toBe(false);
    });
  });

  describe("ad projects", () => {
    const AD_PROJECT: Partial<ProjectData> = {
      content_mode: "ad",
      target_duration: 60,
      episodes: [{ episode: 1, title: "", script_file: "scripts/episode_1.json" }],
    };

    it("shows the ad init canvas until a brief or a product exists", () => {
      const view = renderOverview({ projectData: makeProjectData({ ...AD_PROJECT, brief: "", products: {} }) });
      expect(screen.getByTestId("ad-init-canvas")).toBeInTheDocument();

      view.rerender({ projectData: makeProjectData({ ...AD_PROJECT, brief: "卖点" }) });
      expect(screen.queryByTestId("ad-init-canvas")).not.toBeInTheDocument();
    });

    it("leaves out the episode count and lists merchandise among the assets", () => {
      renderOverview({
        projectData: makeProjectData({
          ...AD_PROJECT,
          brief: "卖点",
          status: {
            needs_repair: false,
            repair_reason: null,
            source_remaining: false,
            episodes_summary: {} as never,
            assets: { product: { total: 1, available: 0, stale: 0 } },
          },
        }),
      });

      expect(screen.getByText("广告/短片 · 竖屏 9:16")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "商品 0/1" })).toHaveAttribute("href", "/products");
    });

    it("keeps the creative brief on the overview and saves brief with a custom target duration", async () => {
      const update = vi.spyOn(API, "updateProject").mockResolvedValue({ success: true, project: {} as ProjectData });
      renderOverview({
        projectName: "ad-demo",
        projectData: makeProjectData({
          ...AD_PROJECT,
          brief: "",
          products: { 冰饮: { description: "柠檬气泡水" } } as unknown as ProjectData["products"],
        }),
      });
      const card = screen.getByRole("region", { name: "创作灵感" });

      fireEvent.click(within(card).getByRole("button", { name: "编辑" }));
      fireEvent.change(within(card).getByRole("textbox", { name: "创作灵感" }), { target: { value: "夏日解渴" } });
      fireEvent.click(within(card).getByRole("radio", { name: "自定义" }));
      fireEvent.change(within(card).getByRole("spinbutton", { name: "自定义目标总时长（秒）" }), {
        target: { value: "45" },
      });
      fireEvent.click(within(card).getByRole("button", { name: "保存" }));

      await waitFor(() => expect(update).toHaveBeenCalledWith("ad-demo", { brief: "夏日解渴", target_duration: 45 }));
    });
  });
});
