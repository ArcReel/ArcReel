import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { ReferenceVideoCanvas } from "./ReferenceVideoCanvas";
import { useReferenceVideoStore } from "@/stores/reference-video-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";
import { API } from "@/api";
import type { ReferenceVideoUnit } from "@/types";
import type { ProjectData } from "@/types";

function mkUnit(id: string, shotText = "x"): ReferenceVideoUnit {
  return {
    unit_id: id,
    shots: [{ duration: 3, text: shotText }],
    references: [],
    duration_seconds: 3,
    duration_override: false,
    transition_to_next: "cut",
    note: null,
    generated_assets: {
      storyboard_image: null,
      storyboard_last_image: null,
      grid_id: null,
      grid_cell_index: null,
      video_clip: null,
      video_uri: null,
      status: "pending",
    },
  };
}

const STUB_PROJECT: ProjectData = {
  title: "p",
  content_mode: "narration",
  style: "",
  episodes: [],
  characters: {},
  scenes: {},
  props: {},
};

describe("ReferenceVideoCanvas", () => {
  beforeEach(() => {
    useReferenceVideoStore.setState({ unitsByEpisode: {}, selectedUnitId: null, loading: false, error: null });
    useProjectsStore.setState({ currentProjectName: "proj", currentProjectData: STUB_PROJECT });
    useTasksStore.setState({ tasks: [], connected: false });
    useAppStore.setState({ toast: null });
  });
  afterEach(() => vi.restoreAllMocks());

  it("loads units on mount and renders the list", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1"), mkUnit("E1U2")] });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
    expect(screen.getByTestId("unit-row-E1U2")).toBeInTheDocument();
  });

  it("auto-selects first unit on load and shows preview generate button", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generate video|生成视频/ })).toBeInTheDocument();
    });
  });

  it("renders the ReferenceVideoCard textarea once auto-selected", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1")],
    });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    const ta = await screen.findByRole("combobox");
    expect((ta as HTMLTextAreaElement).value).toContain("Shot 1 (3s): x");
  });

  it("remounts the card so textarea shows the new unit's prompt when selection changes", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1", "hello from A"), mkUnit("E1U2", "hello from B")],
    });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    const taA = (await screen.findByRole("combobox")) as HTMLTextAreaElement;
    expect(taA.value).toContain("hello from A");
    fireEvent.click(screen.getByTestId("unit-row-E1U2"));
    await waitFor(() => {
      expect((screen.getByRole("combobox") as HTMLTextAreaElement).value).toContain("hello from B");
    });
  });

  it("adds a new unit via the store when the button is clicked", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [] });
    const addSpy = vi.spyOn(API, "addReferenceVideoUnit").mockResolvedValue({ unit: mkUnit("E1U1") });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /New Unit|新建 Unit/ })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /New Unit|新建 Unit/ }));
    await waitFor(() => expect(addSpy).toHaveBeenCalled());
  });

  // 主 tab：视频单元 / 拆分预处理。默认 "视频单元"，即 UnitList 区域可见。
  it("renders the main tab bar with 'units' selected by default", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
    const tabs = screen.getAllByRole("tab");
    // 主 tab 至少 2 个；小屏 stackPreview 还会再加 2 个 sub-tab
    expect(tabs.length).toBeGreaterThanOrEqual(2);
    const unitsTab = screen.getByRole("tab", { name: /Video units|视频单元/ });
    expect(unitsTab).toHaveAttribute("aria-selected", "true");
  });

  it("switches main tab between units and preprocess", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
    const unitsTab = screen.getByRole("tab", { name: /Video units|视频单元/ });
    const preprocTab = screen.getByRole("tab", { name: /Splitting preprocess|拆分预处理/ });
    fireEvent.click(preprocTab);
    expect(preprocTab).toHaveAttribute("aria-selected", "true");
    expect(unitsTab).toHaveAttribute("aria-selected", "false");
    // 拆分预处理 tab 下 UnitList 不渲染
    expect(screen.queryByTestId("unit-row-E1U1")).not.toBeInTheDocument();
    fireEvent.click(unitsTab);
    expect(unitsTab).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
  });

  // 默认选中第一个 unit，避免出现 "有 units 但 editor 区域显示占位" 的不一致状态。
  it("resets a stale selectedUnitId (e.g. from a previous episode) to the first unit of current units", async () => {
    // 模拟切换 episode 后残留的旧 selectedUnitId
    useReferenceVideoStore.setState({ selectedUnitId: "E99U42" });
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1", "first"), mkUnit("E1U2", "second")],
    });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => {
      expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U1");
    });
    const ta = (await screen.findByRole("combobox")) as HTMLTextAreaElement;
    expect(ta.value).toContain("first");
  });

  // v3 重构：preproc 入口从二级页面跳转改为主 tab 切换；不再有"返回编辑"按钮。
  // 切到拆分预处理 tab 后，UnitList 被隐藏，PreprocessingView inline 渲染。
  it("inline-renders preprocessing view via the main tab", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1"), mkUnit("E1U2")],
    });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
    const preprocTab = screen.getByRole("tab", { name: /Splitting preprocess|拆分预处理/ });
    fireEvent.click(preprocTab);
    // tab 切换后 UnitList 被隐藏；PreprocessingView 由调用方控制 toolbar 已不再显示返回按钮（直接 inline）
    expect(preprocTab).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByTestId("unit-row-E1U1")).not.toBeInTheDocument();
  });

  // optimistic：任务队列 3s 轮询间隙内按钮也要立刻反馈 busy，否则用户
  // 会误以为"点了没反应"继续点击造成重复入队。
  it("flips the generate button to busy optimistically before the task poll picks it up", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    // 用 deferred promise 模拟 202 响应尚未回来的中间态
    let resolveGen: (v: { task_id: string; deduped: boolean }) => void = () => {};
    const genSpy = vi.spyOn(API, "generateReferenceVideoUnit").mockReturnValue(
      new Promise((resolve) => {
        resolveGen = resolve;
      }),
    );
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    const btn = await screen.findByRole("button", { name: /Generate video|生成视频/ });
    // 点击前 tasks store 为空，按钮启用
    expect(btn).not.toBeDisabled();
    fireEvent.click(btn);
    // 立刻：按钮 disabled，显示 "Generating…/生成中"
    await waitFor(() => expect(screen.getByRole("button", { name: /Generating|生成中/ })).toBeDisabled());
    // 收尾：让 generate promise 完成 + info toast 冒出
    resolveGen({ task_id: "t1", deduped: false });
    await waitFor(() => expect(genSpy).toHaveBeenCalled());
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toMatch(/Queued for generation|已加入生成队列/);
    });
  });

  // 后台任务失败通知已统一迁移到全局 useTaskFailureNotifications hook（转变驱动 /
  // 历史失败不重报 / 同一失败只报一次回归均在那里覆盖），见
  // hooks/useTaskFailureNotifications.test.tsx。此处只验证回跳消费。

  // 通知回跳：收到 reference_unit scroll target 时切到 units tab 并选中对应 unit。
  it("selects the unit on a reference_unit scroll target", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1"), mkUnit("E1U2")],
    });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U1"));

    useAppStore.getState().triggerScrollTo({ type: "reference_unit", id: "E1U2", route: "/episodes/1" });

    await waitFor(() => expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U2"));
    expect(useAppStore.getState().scrollTarget).toBeNull();
    expect(screen.getByRole("tab", { name: /Video units|视频单元/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  // 慢网/冷启动回归：units 仍在加载（loadUnits 未返回）时，即便 target 已过期也不该
  // 提前清除——否则 units 到达后无法再选中目标 unit，"点击通知回跳"失效。
  it("keeps a reference_unit target while units are still loading, even past expiry", async () => {
    let resolveList: (v: { units: ReferenceVideoUnit[] }) => void = () => {};
    vi.spyOn(API, "listReferenceVideoUnits").mockReturnValue(
      new Promise((resolve) => {
        resolveList = resolve;
      }),
    );
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    // 加载中（fetch 挂起，loading=true）下发一个已过期的 target；act 确定性 flush
    // 回跳 effect（避免固定延时——这是否定性断言，waitFor 首检即真无法证明 target
    // 持续存在，setTimeout 又可能在 effect 跑完前就断言导致漏判 bug）。
    await act(async () => {
      useAppStore.getState().triggerScrollTo({
        type: "reference_unit",
        id: "E1U2",
        route: "/episodes/1",
        expires_at: Date.now() - 1,
      });
    });
    // 关键断言：effect 已运行，但加载未完成时不按过期清除，target 仍在
    expect(useAppStore.getState().scrollTarget?.id).toBe("E1U2");
    // units 到达后应命中并选中目标 unit，随后清除 target
    await act(async () => {
      resolveList({ units: [mkUnit("E1U1"), mkUnit("E1U2")] });
    });
    await waitFor(() => expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U2"));
    expect(useAppStore.getState().scrollTarget).toBeNull();
  });

  // 兜底回归：units 加载完成但目标 unit 不存在时，即便此后没有任何依赖变化，
  // 过期 target 也应被一次性定时器清除，不会永久残留 store。
  it("clears an unresolvable reference_unit target after expiry without further updates", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U1"));
    // 目标 unit 不在列表中，给一个略长的过期窗口降低脆弱性
    act(() => {
      useAppStore.getState().triggerScrollTo({
        type: "reference_unit",
        id: "E9U9",
        route: "/episodes/1",
        expires_at: Date.now() + 200,
      });
    });
    // 先断言 target 已写入且未被即时清除——证明走的是定时器路径而非 immediate clear
    expect(useAppStore.getState().scrollTarget?.id).toBe("E9U9");
    // 此后不再产生任何依赖变化，仅靠一次性定时器到期清理
    await waitFor(() => expect(useAppStore.getState().scrollTarget).toBeNull());
  });
});
