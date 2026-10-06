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
import { useAssistantStore } from "@/stores/assistant-store";
import { useCostStore } from "@/stores/cost-store";
import { useOverviewGenerateStore } from "@/stores/overview-generate-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { CostEstimateResponse, ProjectData } from "@/types";

import { OverviewCanvas } from "./OverviewCanvas";
import { useHandoffTipStore } from "./useHandoffTip";

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

function StoreOverview() {
  const projectData = useProjectsStore((state) => state.currentProjectData);
  return <OverviewCanvas projectName="demo" projectData={projectData} />;
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
    useOverviewGenerateStore.setState(useOverviewGenerateStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getProject").mockResolvedValue({ project: makeProjectData(), scripts: {} });
  });

  it("summarizes mode, aspect ratio, episode count and script length in the header", () => {
    renderOverview();

    expect(screen.getByRole("heading", { level: 1, name: "Demo" })).toBeInTheDocument();
    expect(screen.getByText("剧情演绎 · 竖屏 9:16 · 2 集 · 脚本时长 1:30")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "项目设置" })).toHaveAttribute("href", "/app/projects/demo/settings");
  });

  it("rounds a fractional script length before splitting it into minutes and seconds", () => {
    renderOverview({
      projectData: makeProjectData({
        episodes: [
          { episode: 1, title: "EP1", script_file: "scripts/episode_1.json", duration_seconds: 59.6 },
          { episode: 2, title: "EP2", script_file: "scripts/episode_2.json", duration_seconds: 60 },
        ],
      }),
    });

    expect(screen.getByText("剧情演绎 · 竖屏 9:16 · 2 集 · 脚本时长 2:00")).toBeInTheDocument();
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
    expect(screen.getByText(/本机没有这个项目的生成记录/)).toBeInTheDocument();
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

  it("marks only the unpriced media type as unknown in the details popover", async () => {
    const user = userEvent.setup();
    const relay = { provider: "custom-3", provider_name: "Relay", count: 1 } as const;
    setCost(
      "demo",
      {},
      {
        project_totals: { estimate: { image: { USD: 2 } }, actual: { image: { USD: 4 } } },
        unpriced: {
          estimate: [{ ...relay, call_type: "audio", model: "tts" }],
          actual: [
            { ...relay, call_type: "image", model: "img" },
            { ...relay, call_type: "video", model: "vid" },
          ],
        },
      },
    );
    renderOverview();

    await user.click(screen.getByRole("button", { name: "明细" }));
    const popover = await screen.findByRole("dialog", { name: "费用明细" });
    // 按预估、已花的列序取同名行的值；某列没有这一行时不出现
    const values = (label: string) =>
      within(popover)
        .getAllByText(label, { selector: "dt" })
        .map((term) => term.nextElementSibling?.textContent);

    // 同一类型部分没计价时仍显示已计价的金额
    expect(values("分镜")).toEqual(["$2.00", "$4.00"]);
    expect(values("视频")).toEqual(["0", "—"]);
    // 只有没计价的配音时也要列出这一行，不能让它从明细里消失
    expect(values("旁白配音")).toEqual(["—"]);
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

    expect(generate).toHaveBeenCalledWith("demo");
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

  it("keeps reading the source after leaving the overview mid-generation and fills in the result on return", async () => {
    const user = userEvent.setup();
    let finishGenerate!: () => void;
    vi.spyOn(API, "generateOverview").mockImplementation(
      () =>
        new Promise((resolve) => {
          finishGenerate = () => resolve({ success: true, overview: {} as never });
        }),
    );
    const source = { whole_source_files: [{ source_file: "source/novel.txt" }] };
    useProjectsStore.getState().setCurrentProject("demo", makeProjectData({ ...EMPTY_PROJECT, ...source }));
    vi.spyOn(API, "getProject").mockResolvedValue({ project: makeProjectData(source), scripts: {} });
    const first = render(withRouter(<StoreOverview />));

    await user.click(screen.getByRole("button", { name: "从原文生成" }));
    expect(screen.getByRole("status")).toHaveTextContent("正在读取原文…");
    first.unmount();

    // 离开期间服务端仍在生成：回到概览时接着显示读取中
    render(withRouter(<StoreOverview />));
    expect(screen.getByRole("status")).toHaveTextContent("正在读取原文…");

    await act(async () => finishGenerate());

    await waitFor(() => expect(screen.queryByText("正在读取原文…")).not.toBeInTheDocument());
    expect(screen.getByRole("textbox", { name: "梗概" })).toHaveValue("summary");
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
    expect(within(alert).getByRole("link", { name: "去登记最大输出长度" })).toBeInTheDocument();
    // 失败后字段回来，可以手写或再试一次
    expect(screen.getByRole("textbox", { name: "梗概" })).toBeInTheDocument();
  });

  it("says the story setting was generated when only the follow-up refresh fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "generateOverview").mockResolvedValue({ success: true, overview: {} as never });
    vi.spyOn(API, "getProject").mockRejectedValue(new Error("offline"));
    renderOverview({
      projectData: makeProjectData({ ...EMPTY_PROJECT, whole_source_files: [{ source_file: "source/novel.txt" }] }),
    });

    await user.click(screen.getByRole("button", { name: "从原文生成" }));

    // 生成已落盘、只是没取回：不能把它报成生成失败，也不能静默停在旧内容上引人再生成一次
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("故事设定已生成，但页面数据刷新失败，请刷新页面查看最新结果。");
    expect(alert).not.toHaveTextContent("生成失败");
  });

  it("drops the refresh warning once the project data loads again", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "generateOverview").mockResolvedValue({ success: true, overview: {} as never });
    vi.spyOn(API, "getProject").mockRejectedValue(new Error("offline"));
    useProjectsStore
      .getState()
      .setCurrentProject("demo", makeProjectData({ ...EMPTY_PROJECT, whole_source_files: [{ source_file: "source/novel.txt" }] }));
    render(withRouter(<StoreOverview />));

    await user.click(screen.getByRole("button", { name: "从原文生成" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("页面数据刷新失败");

    // 之后重新加载成功（如切走再回来），数据已带上生成结果，提示不再挂着
    act(() => useProjectsStore.getState().setCurrentProject("demo", makeProjectData()));
    expect(screen.queryByText(/页面数据刷新失败/)).not.toBeInTheDocument();
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
      expect(screen.getByText("novel.txt")).toBeInTheDocument();
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
      useProjectsStore.getState().setCurrentProject("demo", makeProjectData(EMPTY_PROJECT));
      vi.spyOn(API, "getProject")
        .mockResolvedValueOnce({
          project: makeProjectData({ ...EMPTY_PROJECT, whole_source_files: [{ source_file: "source/novel.txt" }] }),
          scripts: {},
        })
        .mockResolvedValueOnce({
          project: makeProjectData({ episodes: [], whole_source_files: [{ source_file: "source/novel.txt" }] }),
          scripts: {},
        });
      render(withRouter(<StoreOverview />));

      fireEvent.drop(screen.getByRole("button", { name: /拖入原文/ }), {
        dataTransfer: { files: [new File(["x"], "novel.txt", { type: "text/plain" })] },
      });
      await user.click(screen.getByRole("button", { name: "上传 1 个文件" }));

      expect(await screen.findByText("正在读取原文…")).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: /开始制作/ })).not.toBeInTheDocument();
      expect(screen.getByRole("heading", { level: 1, name: "Demo" })).toBeInTheDocument();

      // 生成完成，项目刷新带回故事设定
      await act(async () => finishGenerate());

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

  describe("handoff tip", () => {
    const TIP = "故事设定已提炼完成。接下来在右侧向 Agent 发送「开始制作」。";

    /** 故事设定在本次会话内由空变为有内容。 */
    function fillStorySetting(projectName = "demo") {
      const view = renderOverview({
        projectName,
        projectData: makeProjectData({ overview: undefined, whole_source_files: [{ source_file: "source/a.txt" }] }),
      });
      view.rerender({ projectName, projectData: makeProjectData() });
      return view;
    }

    beforeEach(() => {
      useHandoffTipStore.setState(useHandoffTipStore.getInitialState(), true);
      useAssistantStore.setState(useAssistantStore.getInitialState(), true);
    });

    it("appears under the story setting once it goes from empty to filled, and stays after leaving the overview", () => {
      const view = fillStorySetting();
      expect(screen.getByText(TIP).closest("[role=status]")).not.toBeNull();

      // 切到别的视图再回来（概览卸载后重新挂载），提示还在
      view.unmount();
      renderOverview();
      expect(screen.getByText(TIP).closest("[role=status]")).not.toBeNull();
    });

    it("goes away for good once dismissed, including after a reload", async () => {
      const user = userEvent.setup();
      const view = fillStorySetting();
      await user.click(screen.getByRole("button", { name: "知道了" }));
      expect(screen.queryByText(TIP)).not.toBeInTheDocument();

      // 刷新：内存里的待显示状态清空，同一项目的故事设定再次由空变为有内容也不再提示
      view.unmount();
      useHandoffTipStore.setState(useHandoffTipStore.getInitialState(), true);
      fillStorySetting();
      expect(screen.queryByText(TIP)).not.toBeInTheDocument();
    });

    it("goes away when the first message is sent to the agent in this project", () => {
      const view = fillStorySetting();

      act(() => useAssistantStore.setState({ currentProject: "other", sending: true }));
      expect(screen.getByText(TIP)).toBeInTheDocument();

      act(() => useAssistantStore.setState({ sending: false }));
      act(() => useAssistantStore.setState({ currentProject: "demo", sending: true }));
      expect(screen.queryByText(TIP)).not.toBeInTheDocument();

      view.unmount();
      useHandoffTipStore.setState(useHandoffTipStore.getInitialState(), true);
      fillStorySetting();
      expect(screen.queryByText(TIP)).not.toBeInTheDocument();
    });

    it("appears when the story setting was filled while the overview was away", () => {
      // 生成在后台继续：离开概览时还是空的，回来时已有内容
      const view = renderOverview({
        projectData: makeProjectData({ overview: undefined, whole_source_files: [{ source_file: "source/a.txt" }] }),
      });
      view.unmount();
      renderOverview();

      expect(screen.getByText(TIP).closest("[role=status]")).not.toBeNull();
    });

    it("goes away when a message is sent to the agent from another view of this project", () => {
      const view = fillStorySetting();
      view.unmount();

      act(() => useAssistantStore.setState({ currentProject: "demo", sending: true }));
      renderOverview();

      expect(screen.queryByText(TIP)).not.toBeInTheDocument();
    });

    it("does not appear once a message was sent to the agent in this project before the story setting filled in", () => {
      act(() => useAssistantStore.setState({ currentProject: "demo", sending: true }));
      fillStorySetting();

      expect(screen.queryByText(TIP)).not.toBeInTheDocument();
    });

    it("does not fire when switching from an empty project to another project that already has a story setting", () => {
      const view = renderOverview({ projectName: "project-a", projectData: makeProjectData(EMPTY_PROJECT) });
      view.rerender({ projectName: "project-b", projectData: makeProjectData() });

      expect(screen.queryByText(TIP)).not.toBeInTheDocument();
    });

    it("does not fire on a read-only project", () => {
      const view = renderOverview({ projectName: "onboarding_demo", projectData: makeProjectData(EMPTY_PROJECT), readOnly: true });
      view.rerender({ projectName: "onboarding_demo", projectData: makeProjectData(), readOnly: true });

      expect(screen.queryByText(TIP)).not.toBeInTheDocument();
      expect(useHandoffTipStore.getState().pending.size).toBe(0);
    });
  });

  describe("ad projects", () => {
    const AD_PROJECT: Partial<ProjectData> = {
      content_mode: "ad",
      target_duration: 60,
      episodes: [{ episode: 1, title: "", script_file: "scripts/episode_1.json" }],
    };

    beforeEach(() => {
      vi.spyOn(API, "getAssetSheetStatus").mockResolvedValue({ assets: [] } as never);
    });

    it("takes the first input on the overview: brief and products, with the story setting left to the video page", async () => {
      renderOverview({ projectData: makeProjectData({ ...AD_PROJECT, brief: "", products: {} }) });

      expect(screen.getByRole("region", { name: "创作灵感" })).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "故事设定" })).not.toBeInTheDocument();
      const products = screen.getByRole("region", { name: "商品" });
      expect(within(products).getByText("还没有商品")).toBeInTheDocument();

      fireEvent.click(within(products).getByRole("button", { name: "添加商品" }));
      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });

    it("opens a listed product in the detail sheet", async () => {
      renderOverview({
        projectData: makeProjectData({
          ...AD_PROJECT,
          products: { 冰饮: { description: "柠檬气泡水" } } as unknown as ProjectData["products"],
        }),
      });
      const products = screen.getByRole("region", { name: "商品" });
      expect(within(products).getByRole("link", { name: "查看全部" })).toHaveAttribute("href", "/products");

      fireEvent.click(within(products).getByRole("button", { name: /冰饮/ }));
      const sheet = await screen.findByRole("dialog");
      expect(within(sheet).getByDisplayValue("柠檬气泡水")).toBeInTheDocument();
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

    it("saves the brief together with a custom target duration", async () => {
      const update = vi.spyOn(API, "updateProject").mockResolvedValue({ success: true, project: {} as ProjectData });
      renderOverview({ projectName: "ad-demo", projectData: makeProjectData({ ...AD_PROJECT, brief: "" }) });
      const card = screen.getByRole("region", { name: "创作灵感" });

      fireEvent.change(within(card).getByRole("textbox", { name: "创作灵感" }), { target: { value: " 夏日解渴 " } });
      fireEvent.click(within(card).getByRole("radio", { name: "自定义" }));
      fireEvent.change(within(card).getByRole("spinbutton", { name: "自定义目标总时长（秒）" }), {
        target: { value: "45" },
      });
      fireEvent.click(within(card).getByRole("button", { name: "保存" }));

      await waitFor(() => expect(update).toHaveBeenCalledWith("ad-demo", { brief: "夏日解渴", target_duration: 45 }));
    });

    it("sends only the brief when the target duration was left alone", async () => {
      const update = vi.spyOn(API, "updateProject").mockResolvedValue({ success: true, project: {} as ProjectData });
      renderOverview({ projectName: "ad-demo", projectData: makeProjectData({ ...AD_PROJECT, brief: "旧灵感" }) });
      const card = screen.getByRole("region", { name: "创作灵感" });

      fireEvent.change(within(card).getByRole("textbox", { name: "创作灵感" }), { target: { value: "夏日解渴" } });
      fireEvent.click(within(card).getByRole("button", { name: "保存" }));

      await waitFor(() => expect(update).toHaveBeenCalledWith("ad-demo", { brief: "夏日解渴" }));
    });

    it("warns that the brief was saved when refreshing the project afterwards fails", async () => {
      vi.spyOn(API, "updateProject").mockResolvedValue({ success: true, project: {} as ProjectData });
      vi.spyOn(API, "getProject").mockRejectedValue(new Error("offline"));
      renderOverview({ projectName: "ad-demo", projectData: makeProjectData({ ...AD_PROJECT, brief: "旧灵感" }) });
      const card = screen.getByRole("region", { name: "创作灵感" });

      fireEvent.change(within(card).getByRole("textbox", { name: "创作灵感" }), { target: { value: "夏日解渴" } });
      fireEvent.click(within(card).getByRole("button", { name: "保存" }));

      await waitFor(() =>
        expect(useAppStore.getState().toast).toMatchObject({
          text: "操作已完成，但页面数据刷新失败，请手动刷新查看最新状态",
          tone: "warning",
        }),
      );
    });

    it("keeps an invalid custom duration unsaved and says why", async () => {
      const update = vi.spyOn(API, "updateProject");
      renderOverview({ projectName: "ad-demo", projectData: makeProjectData({ ...AD_PROJECT, brief: "旧灵感" }) });
      const card = screen.getByRole("region", { name: "创作灵感" });

      fireEvent.click(within(card).getByRole("radio", { name: "自定义" }));
      fireEvent.click(within(card).getByRole("button", { name: "保存" }));

      // 档位选择器行内的说明之外，提示条也写明没有保存的原因
      await waitFor(() => expect(within(card).getAllByText(/目标总时长须为正整数秒/)).toHaveLength(2));
      expect(update).not.toHaveBeenCalled();
    });
  });
});
