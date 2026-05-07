import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ReferenceVideoCanvas } from "./ReferenceVideoCanvas";
import { useReferenceVideoStore } from "@/stores/reference-video-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";
import { API } from "@/api";
import { makeTask } from "@/test/factories";
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

  // #370 optimistic：任务队列 3s 轮询间隙内按钮也要立刻反馈 busy，否则用户
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

  // #370 failed-toast：只有当我们观测到 task 从 non-failed 转变到 failed 时才
  // 冒泡——模拟"这一次 poll 它还是 queued，下一次 poll 就成了 failed"。
  it("surfaces an error toast when a reference_video task transitions from queued to failed", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
    // 第一轮 poll：任务处于 queued
    useTasksStore.setState({
      tasks: [makeTask({ status: "queued", resource_id: "E1U1", task_id: "t-fail-1" })],
    });
    // 等一个 tick 让 transition effect 把 queued 记入 prevStatus
    await new Promise((r) => setTimeout(r, 20));
    expect(useAppStore.getState().toast).toBeNull();
    // 第二轮 poll：worker 把任务标记为 failed
    useTasksStore.setState({
      tasks: [
        makeTask({
          status: "failed",
          error_message: "boom",
          resource_id: "E1U1",
          task_id: "t-fail-1",
        }),
      ],
    });
    await waitFor(() => {
      const toast = useAppStore.getState().toast;
      expect(toast?.tone).toBe("error");
      expect(toast?.text).toMatch(/E1U1/);
      expect(toast?.text).toMatch(/boom/);
    });
  });

  // #370 回归保护：进页面时队列里残留的历史失败任务（我们没观测到转变）不应该
  // 再次冒泡 toast——这是状态驱动 → 转变驱动 重构的关键合同。
  it("does not toast historical failures present on first observation", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    // 预先塞一条已经是 failed 的历史任务，模拟"打开页面后 useTasksSSE 首次轮询
    // 就把 DB 里的历史失败全 dump 回来"
    useTasksStore.setState({
      tasks: [
        makeTask({
          status: "failed",
          error_message: "不支持的资源类型: reference_videos",
          resource_id: "E1U1",
          task_id: "t-historical",
          finished_at: "2026-04-20T10:00:00Z",
        }),
      ],
    });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
    await new Promise((r) => setTimeout(r, 20));
    // 关键断言：首次观测就已 failed 的任务不产生 toast
    expect(useAppStore.getState().toast).toBeNull();
  });

  // #370 同一个失败任务在后续 poll 里反复出现（worker 不会清理），只应 toast 一次。
  it("does not re-toast the same failed task across multiple poll cycles", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
    // queued → failed 触发一次 toast
    useTasksStore.setState({
      tasks: [makeTask({ status: "queued", resource_id: "E1U1", task_id: "t-once" })],
    });
    await new Promise((r) => setTimeout(r, 20));
    useTasksStore.setState({
      tasks: [makeTask({ status: "failed", error_message: "x", resource_id: "E1U1", task_id: "t-once" })],
    });
    await waitFor(() => expect(useAppStore.getState().toast?.tone).toBe("error"));
    // 清掉第一条 toast，模拟用户关掉后下一轮 poll 又带回同一失败记录
    useAppStore.setState({ toast: null });
    useTasksStore.setState({
      tasks: [makeTask({ status: "failed", error_message: "x", resource_id: "E1U1", task_id: "t-once" })],
    });
    await new Promise((r) => setTimeout(r, 20));
    expect(useAppStore.getState().toast).toBeNull();
  });
});
