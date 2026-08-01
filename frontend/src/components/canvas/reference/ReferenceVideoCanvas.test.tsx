import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, act, within } from "@testing-library/react";
import { ReferenceVideoCanvas } from "./ReferenceVideoCanvas";
import { useReferenceVideoStore, referenceVideoCacheKey } from "@/stores/reference-video-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useActiveResourceIds, useLatestTasksByResource, useTasksStore } from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";
import { API } from "@/api";
import type { ReferenceVideoUnit } from "@/types";
import type { ProjectData } from "@/types";

// useActiveResourceIds / useLatestTasksByResource 默认包裹真实实现，仅在个别用例里
// 冻结返回值模拟「响应式信号尚未追上真实 store」的场景，验证提交 handler 不依赖它们、
// 独立用 getState() 新鲜读 store。
const mockHolder = vi.hoisted(() => ({
  realActiveResourceIds: undefined as unknown as typeof import("@/stores/tasks-store").useActiveResourceIds,
  realLatestTasksByResource:
    undefined as unknown as typeof import("@/stores/tasks-store").useLatestTasksByResource,
}));
vi.mock("@/stores/tasks-store", async () => {
  const actual = await vi.importActual<typeof import("@/stores/tasks-store")>("@/stores/tasks-store");
  mockHolder.realActiveResourceIds = actual.useActiveResourceIds;
  mockHolder.realLatestTasksByResource = actual.useLatestTasksByResource;
  return {
    ...actual,
    useActiveResourceIds: vi.fn(actual.useActiveResourceIds),
    useLatestTasksByResource: vi.fn(actual.useLatestTasksByResource),
  };
});

function mkUnit(id: string, shotText = "x"): ReferenceVideoUnit {
  return {
    unit_id: id,
    shots: [{ text: shotText }],
    references: [],
    duration_seconds: 3,
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
      video_generated_at: null,
    },
  };
}

// 单元预览面板的生成 CTA。锚定行首把批量入口「批量生成视频」排除在外——两者都含
// 「生成视频」，不锚定会按 DOM 顺序先匹配到批量按钮，测到的就不是这条提交路径。
const UNIT_GENERATE_CTA = /^(Generate video|生成视频)/;

function runningTask(unitId: string) {
  return {
    task_id: `task-${unitId}`,
    project_name: "proj",
    task_type: "reference_video",
    resource_id: unitId,
    status: "running",
    updated_at: "2026-06-12T10:00:00Z",
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
    vi.mocked(useActiveResourceIds).mockImplementation(mockHolder.realActiveResourceIds);
    vi.mocked(useLatestTasksByResource).mockImplementation(mockHolder.realLatestTasksByResource);
    useReferenceVideoStore.setState({ unitsByEpisode: {}, selectedUnitId: null, loading: false, error: null });
    useProjectsStore.setState({ currentProjectName: "proj", currentProjectData: STUB_PROJECT });
    // 乐观标记由入队动作层写入且跨测试共享同一 store 实例，须一并重置
    useTasksStore.setState({
      tasks: [],
      connected: false,
      optimisticActive: new Set(),
      optimisticActiveScriptFile: new Set(),
    });
    useAppStore.setState({ toast: null });
    // 时长取档预检默认「与剧本编排一致」：生成入口无确认步骤，与改动前的路径等价
    vi.spyOn(API, "precheckReferenceVideoDuration").mockResolvedValue({
      needs_confirmation: false,
      script_duration: 3,
      request_duration: 3,
      adjustment: "exact",
    });
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
    expect((ta as HTMLTextAreaElement).value).toContain("x");
  });

  // 时长是 unit 级单一真相：下拉档位来自模型能力声明，选中即单独 PATCH（不牵连正文草稿）
  it("renders the unit duration dropdown from the model's declared slots and patches on change", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    const patchSpy = vi
      .spyOn(API, "patchReferenceVideoUnit")
      .mockResolvedValue({ unit: { ...mkUnit("E1U1"), duration_seconds: 8 } });
    render(
      <ReferenceVideoCanvas projectName="proj" episode={1} durationOptions={[3, 8]} durationOptionsNoReference={[3, 8]} />,
    );
    const select = (await screen.findByRole("combobox", {
      name: /Duration|时长/,
    })) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["3", "8"]);
    fireEvent.change(select, { target: { value: "8" } });
    await waitFor(() =>
      expect(patchSpy).toHaveBeenCalledWith("proj", 1, "E1U1", { duration_seconds: 8 }),
    );
  });

  // 换模型后档位收窄，已保存的越界秒数仍要留在选项里，否则下拉会把它静默改写
  it("keeps an out-of-slot saved duration as an option", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    render(
      <ReferenceVideoCanvas projectName="proj" episode={1} durationOptions={[4, 8]} durationOptionsNoReference={[4, 8]} />,
    );
    const select = (await screen.findByRole("combobox", {
      name: /Duration|时长/,
    })) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["3", "4", "8"]);
    expect(select.value).toBe("3");
  });

  // 参考图约束按 unit 生效：不带 references 的 unit 不应因同集内其它带图 unit 而被收窄。
  it("offers the no-reference tier set for a unit without references", async () => {
    const unit = mkUnit("E1U1");
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit] });
    render(
      <ReferenceVideoCanvas
        projectName="proj"
        episode={1}
        durationOptions={[8]}
        durationOptionsNoReference={[4, 8]}
      />,
    );
    const select = (await screen.findByRole("combobox", {
      name: /Duration|时长/,
    })) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["3", "4", "8"]);
  });

  it("offers the reference-narrowed tier set for a unit with references", async () => {
    const unit = { ...mkUnit("E1U1"), references: [{ type: "character" as const, name: "王" }] };
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit] });
    render(
      <ReferenceVideoCanvas
        projectName="proj"
        episode={1}
        durationOptions={[8]}
        durationOptionsNoReference={[4, 8]}
      />,
    );
    const select = (await screen.findByRole("combobox", {
      name: /Duration|时长/,
    })) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["3", "8"]);
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

  // preproc 入口从二级页面跳转改为主 tab 切换；切到拆分预处理 tab 后 UnitList 被隐藏，
  // step1 审核 gate（ScriptReviewGate，contentMode=reference_video）inline 渲染。
  it("inline-renders the step1 review gate via the main tab", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1"), mkUnit("E1U2")],
    });
    vi.spyOn(API, "getScriptReview").mockResolvedValue({
      episode: 1,
      content_mode: "narration",
      status: "pending_review",
      fingerprint: "fp",
      confirmed_at: null,
      content: { units: [{ unit_id: "E1U1", shots: [{ text: "shot text" }], references: [], duration_seconds: 5 }] },
    });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
    const preprocTab = screen.getByRole("tab", { name: /Splitting preprocess|拆分预处理/ });
    fireEvent.click(preprocTab);
    expect(preprocTab).toHaveAttribute("aria-selected", "true");
    // UnitList 被隐藏，改由 gate 渲染 step1 结构化中间态（shot 文本进入可编辑 textarea）
    expect(screen.queryByTestId("unit-row-E1U1")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByDisplayValue("shot text")).toBeInTheDocument());
  });

  // step2 剧本未生成时（仅 segmented）units 端点无脚本可拆、会 404：默认落 preproc tab
  // 且不发起 units 请求，避免用户先看到一个报错的 Unit 面板（回归 Codex P2）。
  it("defaults to preproc tab and skips loadUnits when hasScript is false", async () => {
    const listSpy = vi.spyOn(API, "listReferenceVideoUnits");
    vi.spyOn(API, "getScriptReview").mockResolvedValue({
      episode: 1,
      content_mode: "narration",
      status: "pending_review",
      fingerprint: "fp",
      confirmed_at: null,
      content: { units: [{ unit_id: "E1U1", shots: [{ text: "shot text" }], references: [], duration_seconds: 5 }] },
    });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} hasScript={false} />);
    const preprocTab = await screen.findByRole("tab", { name: /Splitting preprocess|拆分预处理/ });
    expect(preprocTab).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(screen.getByDisplayValue("shot text")).toBeInTheDocument());
    expect(listSpy).not.toHaveBeenCalled();
  });

  it("switches to units tab and fetches once hasScript flips true", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    vi.spyOn(API, "getScriptReview").mockResolvedValue({
      episode: 1,
      content_mode: "narration",
      status: "pending_review",
      fingerprint: "fp",
      confirmed_at: null,
      content: { units: [] },
    });
    const { rerender } = render(<ReferenceVideoCanvas projectName="proj" episode={1} hasScript={false} />);
    const preprocTab = await screen.findByRole("tab", { name: /Splitting preprocess|拆分预处理/ });
    expect(preprocTab).toHaveAttribute("aria-selected", "true");
    rerender(<ReferenceVideoCanvas projectName="proj" episode={1} hasScript={true} />);
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /Video units|视频单元/ })).toHaveAttribute("aria-selected", "true"),
    );
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
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
    await waitFor(() => expect(genSpy).toHaveBeenCalled());
    // 入队成功（202 返回）后、任务轮询写回前：动作层打乐观标记，按钮立即
    // busy 并显示 "Generating…/生成中"；请求飞行中的双击由后端去重索引兜底
    resolveGen({ task_id: "t1", deduped: false });
    await waitFor(() => expect(screen.getByRole("button", { name: /Generating|生成中/ })).toBeDisabled());
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toMatch(/Queued for generation|已加入生成队列/);
    });
  });

  // 重试路径：旧失败行始终在，statusMap 的乐观分支（!queueRow）不生效，禁用须
  // 直接取占用集，否则入队到任务行落库之间的窗口内按钮可重复点击。
  it("重试失败 unit 后在真实任务行落库前即禁用按钮", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    useTasksStore.setState({
      tasks: [
        {
          task_id: "t1",
          project_name: "proj",
          task_type: "reference_video",
          resource_id: "E1U1",
          status: "failed",
          error_message: "供应商拒绝",
          updated_at: "2026-06-12T10:00:00Z",
        },
      ] as never,
    });
    let resolveGen: (v: { task_id: string; deduped: boolean }) => void = () => {};
    const genSpy = vi.spyOn(API, "generateReferenceVideoUnit").mockReturnValue(
      new Promise((resolve) => {
        resolveGen = resolve;
      }),
    );
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);

    const retry = await screen.findByRole("button", { name: /Retry generation|重试生成/ });
    fireEvent.click(retry);
    await waitFor(() => expect(genSpy).toHaveBeenCalled());

    // 请求在途（响应未回、动作层乐观打标尚未落）时按钮就必须已禁用——AC 要求的
    // 窗口起点是「请求发出」，不是「响应返回」。
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generating|生成中/ })).toBeDisabled();
    });
    // 旧任务行仍是 failed，statusMap 未变；预览区不能同时叠出「生成中」占位与
    // 旧失败覆盖层——inFlight 应压过 failed 的展示（回归 Codex P2）。
    expect(screen.queryByText(/Generation failed|生成失败/)).not.toBeInTheDocument();
    resolveGen({ task_id: "t2", deduped: false });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generating|生成中/ })).toBeDisabled();
    });
    expect(screen.queryByText(/Generation failed|生成失败/)).not.toBeInTheDocument();
  });

  // 重新生成路径：旧成功行始终在，statusMap 的乐观分支不生效，同样需要按占用集
  // 独立禁用，不能仅靠 statusMap 派生的 status。
  it("重新生成已完成 unit 后在真实任务行落库前即禁用按钮", async () => {
    const unit = mkUnit("E1U1");
    unit.generated_assets.video_clip = "videos/E1U1.mp4";
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit] });
    useTasksStore.setState({
      tasks: [
        {
          task_id: "t1",
          project_name: "proj",
          task_type: "reference_video",
          resource_id: "E1U1",
          status: "succeeded",
          updated_at: "2026-06-12T10:00:00Z",
        },
      ] as never,
    });
    let resolveGen: (v: { task_id: string; deduped: boolean }) => void = () => {};
    const genSpy = vi.spyOn(API, "generateReferenceVideoUnit").mockReturnValue(
      new Promise((resolve) => {
        resolveGen = resolve;
      }),
    );
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);

    const regenerate = await screen.findByRole("button", { name: /Regenerate video|重新生成视频/ });
    fireEvent.click(regenerate);
    await waitFor(() => expect(genSpy).toHaveBeenCalled());

    // 同上：请求往返期间即须禁用，而非等响应回来才禁用。
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generating|生成中/ })).toBeDisabled();
    });
    resolveGen({ task_id: "t2", deduped: false });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generating|生成中/ })).toBeDisabled();
    });
  });

  // 提交时刻复核：渲染期捕获的占用信号已过期（真实 store 里该 unit 已被别的入口占用），
  // 点击须被 getState() 新鲜读拦下并提示，不得静默再发一次入队请求。
  it("响应式占用信号尚未追上真实 store 时，生成提交仍被 getState() 新鲜读拦截", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    const genSpy = vi
      .spyOn(API, "generateReferenceVideoUnit")
      .mockResolvedValue({ task_id: "t1", deduped: false } as never);
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    // 冻结两个响应式 hook——模拟面板渲染期捕获的 busy/status 未能反映随后落库的任务行
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);

    // 锚定行首，避免匹配到批量入口「批量生成视频」——它是另一条提交路径（见下一个用例）
    const generate = await screen.findByRole("button", { name: UNIT_GENERATE_CTA });
    expect(generate).not.toBeDisabled();

    // 渲染之后、点击之前，另一入口（Agent 入队 / SSE 落库）已占用同一 unit
    act(() => {
      useTasksStore.setState({ tasks: [runningTask("E1U1")] as never });
    });

    fireEvent.click(generate);
    await waitFor(() => expect(pushToast).toHaveBeenCalled());
    expect(genSpy).not.toHaveBeenCalled();
  });

  // 批量入口的保护对象是「全部待生成 unit」：占用的 unit 静默跳过（逐个报错只会刷屏），
  // 没有待生成 unit 时按钮直接禁用——此前禁用条件只看当前选中 unit，与保护对象无关。
  it("批量生成跳过提交时刻已被占用的 unit，不重复入队", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1"), mkUnit("E1U2")],
    });
    const genSpy = vi
      .spyOn(API, "generateReferenceVideoUnit")
      .mockResolvedValue({ task_id: "t9", deduped: false } as never);
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    const batch = await screen.findByRole("button", { name: /Batch generate videos|批量生成视频/ });
    await waitFor(() => expect(batch).not.toBeDisabled());

    // 渲染之后、点击之前，E1U1 已被别的入口占用；E1U2 仍空闲
    act(() => {
      useTasksStore.setState({ tasks: [runningTask("E1U1")] as never });
    });

    fireEvent.click(batch);
    await waitFor(() => expect(genSpy).toHaveBeenCalledTimes(1));
    expect(genSpy).toHaveBeenCalledWith("proj", 1, "E1U2");
  });

  // 上传与生成回写同一个成片文件：文件选择对话框打开期间同一 unit 可能已被占用，
  // 按钮渲染期的禁用态挡不住这段窗口，提交时刻须再用 getState() 新鲜读复核一次。
  it("上传确认时刻复核占用态，命中即拒绝并提示", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    const uploadSpy = vi.spyOn(API, "uploadReferenceUnitVideo");
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    const { container } = render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await screen.findByRole("button", { name: UNIT_GENERATE_CTA });
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();

    // 选择文件之后、确认之前，该 unit 已被别的入口占用
    act(() => {
      useTasksStore.setState({ tasks: [runningTask("E1U1")] as never });
    });

    fireEvent.change(input!, { target: { files: [new File(["x"], "clip.mp4", { type: "video/mp4" })] } });

    await waitFor(() => expect(pushToast).toHaveBeenCalled());
    expect(uploadSpy).not.toHaveBeenCalled();
  });

  // 上传不产生任务行，进不了 tasks-store 占用集，故由画布自记。它必须进入提交时刻的
  // 复核口径：否则上传在途期间批量生成仍会入队同一 unit，两条路径并发写同一个成片文件。
  it("批量生成跳过上传在途的 unit", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1"), mkUnit("E1U2")],
    });
    // 上传挂起不 resolve，模拟「请求已发出、尚未落盘」的窗口
    vi.spyOn(API, "uploadReferenceUnitVideo").mockReturnValue(new Promise(() => {}) as never);
    const genSpy = vi
      .spyOn(API, "generateReferenceVideoUnit")
      .mockResolvedValue({ task_id: "t9", deduped: false } as never);
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    const { container } = render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    const batch = await screen.findByRole("button", { name: /Batch generate videos|批量生成视频/ });
    await waitFor(() => expect(batch).not.toBeDisabled());

    // 选中项默认是 E1U1，其预览面板的上传入口即针对该 unit
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    fireEvent.change(input!, { target: { files: [new File(["x"], "clip.mp4", { type: "video/mp4" })] } });

    fireEvent.click(batch);
    await waitFor(() => expect(genSpy).toHaveBeenCalledTimes(1));
    expect(genSpy).toHaveBeenCalledWith("proj", 1, "E1U2");
  });

  // 时长取档确认：模型只接受离散档位，剧本编排落在档位之间时按能装下它的最小档位生成，
  // 成片不裁剪——秒数不一致这件事必须在入队前讲清楚，由用户决定是否继续。
  describe("时长取档确认", () => {
    const CONFIRM_CTA = /Generate at this length|按此时长生成/;

    function stubPrecheck(precheck: {
      needs_confirmation: boolean;
      script_duration: number;
      request_duration: number;
      adjustment: "exact" | "up" | "down" | "unconstrained";
    }) {
      vi.spyOn(API, "precheckReferenceVideoDuration").mockResolvedValue(precheck);
    }

    it("申请秒数与剧本编排不一致时先确认，确认后才入队", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
      const genSpy = vi
        .spyOn(API, "generateReferenceVideoUnit")
        .mockResolvedValue({ task_id: "t1", deduped: false } as never);
      stubPrecheck({
        needs_confirmation: true,
        script_duration: 5,
        request_duration: 8,
        adjustment: "up",
      });

      render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
      fireEvent.click(await screen.findByRole("button", { name: UNIT_GENERATE_CTA }));

      // 确认前不得入队
      const confirm = await screen.findByRole("button", { name: CONFIRM_CTA });
      expect(genSpy).not.toHaveBeenCalled();
      // 弹窗内容含剧本编排秒数、申请秒数与「成片更长」的说明
      expect(screen.getByText(/5 秒|5s/)).toBeInTheDocument();
      expect(screen.getByText(/8 秒|8s/)).toBeInTheDocument();
      expect(screen.getByText(/长 3 秒|3s longer/)).toBeInTheDocument();

      fireEvent.click(confirm);
      await waitFor(() => expect(genSpy).toHaveBeenCalledWith("proj", 1, "E1U1"));
    });

    it("取消确认则不入队", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
      const genSpy = vi
        .spyOn(API, "generateReferenceVideoUnit")
        .mockResolvedValue({ task_id: "t1", deduped: false } as never);
      stubPrecheck({
        needs_confirmation: true,
        script_duration: 5,
        request_duration: 8,
        adjustment: "up",
      });

      render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
      fireEvent.click(await screen.findByRole("button", { name: UNIT_GENERATE_CTA }));
      await screen.findByRole("button", { name: CONFIRM_CTA });

      fireEvent.click(screen.getByRole("button", { name: /Cancel|取消/ }));

      await waitFor(() =>
        expect(screen.queryByRole("button", { name: CONFIRM_CTA })).not.toBeInTheDocument(),
      );
      expect(genSpy).not.toHaveBeenCalled();
    });

    it("总时长超过最大档位时说明成片短于剧本编排", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
      vi.spyOn(API, "generateReferenceVideoUnit").mockResolvedValue({
        task_id: "t1",
        deduped: false,
      } as never);
      stubPrecheck({
        needs_confirmation: true,
        script_duration: 20,
        request_duration: 12,
        adjustment: "down",
      });

      render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
      fireEvent.click(await screen.findByRole("button", { name: UNIT_GENERATE_CTA }));

      await screen.findByRole("button", { name: CONFIRM_CTA });
      expect(screen.getByText(/短 8 秒|8s shorter/)).toBeInTheDocument();
      expect(screen.getByText(/放不下完整的剧本编排|cannot hold the full script/)).toBeInTheDocument();
    });

    it("批量入口把需确认的单元聚合成一次确认，不逐个弹窗", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2")],
      });
      const genSpy = vi
        .spyOn(API, "generateReferenceVideoUnit")
        .mockResolvedValue({ task_id: "t9", deduped: false } as never);
      stubPrecheck({
        needs_confirmation: true,
        script_duration: 3,
        request_duration: 4,
        adjustment: "up",
      });

      render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
      const batch = await screen.findByRole("button", { name: /Batch generate videos|批量生成视频/ });
      await waitFor(() => expect(batch).not.toBeDisabled());
      fireEvent.click(batch);

      const confirm = await screen.findByRole("button", { name: CONFIRM_CTA });
      // 两个单元只弹一次，且确认前一个都没入队
      expect(screen.getAllByRole("button", { name: CONFIRM_CTA })).toHaveLength(1);
      expect(genSpy).not.toHaveBeenCalled();

      fireEvent.click(confirm);
      await waitFor(() => expect(genSpy).toHaveBeenCalledTimes(2));
    });

    // 弹窗停留时长由用户决定，可以很长：其间别处完成的单元若按冻结清单原样提交，队列
    // 去重（只看 queued/running/cancelling）拦不住，会重跑一次生成、重复计费并覆盖成片。
    it("批量确认停留期间已完成的单元不再入队", async () => {
      const u1 = mkUnit("E1U1");
      const u2 = mkUnit("E1U2");
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [u1, u2] });
      const genSpy = vi
        .spyOn(API, "generateReferenceVideoUnit")
        .mockResolvedValue({ task_id: "t9", deduped: false } as never);
      stubPrecheck({
        needs_confirmation: true,
        script_duration: 3,
        request_duration: 4,
        adjustment: "up",
      });

      render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
      const batch = await screen.findByRole("button", { name: /Batch generate videos|批量生成视频/ });
      await waitFor(() => expect(batch).not.toBeDisabled());
      fireEvent.click(batch);
      const confirm = await screen.findByRole("button", { name: CONFIRM_CTA });

      // 弹窗停留期间 E1U1 由别处（Agent / 另一标签页）生成完成并落库
      act(() => {
        useReferenceVideoStore.setState({
          unitsByEpisode: {
            [referenceVideoCacheKey("proj", 1)]: [
              { ...u1, generated_assets: { ...u1.generated_assets, video_clip: "videos/E1U1.mp4" } },
              u2,
            ],
          },
        } as never);
      });

      fireEvent.click(confirm);
      // 先等清单里最后一个单元入队完毕，再断言已完成的那个自始至终没被提交——
      // 直接 waitFor 调用次数为 1 会在第一次调用时就满足，捕获不到多出来的那次。
      await waitFor(() => expect(genSpy).toHaveBeenCalledWith("proj", 1, "E1U2"));
      expect(genSpy).not.toHaveBeenCalledWith("proj", 1, "E1U1");
      expect(genSpy).toHaveBeenCalledTimes(1);
    });

    // 串行入队的每个请求之间也是一段等待窗口：只在循环开始前过滤一次，靠后的单元在等
    // 前几个请求时完成的话照样会被提交。
    it("串行入队途中完成的单元不再入队", async () => {
      const [u1, u2] = [mkUnit("E1U1"), mkUnit("E1U2")];
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [u1, u2] });
      const genSpy = vi
        .spyOn(API, "generateReferenceVideoUnit")
        .mockImplementation(async (_p, _e, unitId) => {
          // 第一个单元的请求飞行期间，E1U2 由别处生成完成并落库
          if (unitId === "E1U1") {
            act(() => {
              useReferenceVideoStore.setState({
                unitsByEpisode: {
                  [referenceVideoCacheKey("proj", 1)]: [
                    u1,
                    { ...u2, generated_assets: { ...u2.generated_assets, video_clip: "videos/E1U2.mp4" } },
                  ],
                },
              } as never);
            });
          }
          return { task_id: "t9", deduped: false } as never;
        });
      stubPrecheck({
        needs_confirmation: true,
        script_duration: 3,
        request_duration: 4,
        adjustment: "up",
      });

      render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
      const batch = await screen.findByRole("button", { name: /Batch generate videos|批量生成视频/ });
      await waitFor(() => expect(batch).not.toBeDisabled());
      fireEvent.click(batch);
      fireEvent.click(await screen.findByRole("button", { name: CONFIRM_CTA }));

      await waitFor(() => expect(genSpy).toHaveBeenCalledWith("proj", 1, "E1U1"));
      expect(genSpy).not.toHaveBeenCalledWith("proj", 1, "E1U2");
      expect(genSpy).toHaveBeenCalledTimes(1);
    });

    // 反向：单元入口的「重新生成」本就要覆盖已有成片，不能被同一条复核挡掉。
    it("单元入口对已有成片的单元仍可重新生成", async () => {
      const ready = mkUnit("E1U1");
      ready.generated_assets.video_clip = "videos/E1U1.mp4";
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [ready] });
      const genSpy = vi
        .spyOn(API, "generateReferenceVideoUnit")
        .mockResolvedValue({ task_id: "t1", deduped: false } as never);
      stubPrecheck({
        needs_confirmation: true,
        script_duration: 5,
        request_duration: 8,
        adjustment: "up",
      });

      render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
      fireEvent.click(await screen.findByRole("button", { name: /Regenerate|重新生成/ }));

      fireEvent.click(await screen.findByRole("button", { name: CONFIRM_CTA }));
      await waitFor(() => expect(genSpy).toHaveBeenCalledWith("proj", 1, "E1U1"));
    });

    // 预检是一段 await 窗口：其间被别处占用的 unit 若仍列进确认清单，就是请用户为一件
    // 做不成的事拍板（确认后才在入队复核处被拒）。弹窗打开时刻同样要复核占用态。
    it("预检期间被占用的单元不列入确认清单", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2"), mkUnit("E1U3")],
      });
      const genSpy = vi
        .spyOn(API, "generateReferenceVideoUnit")
        .mockResolvedValue({ task_id: "t9", deduped: false } as never);
      vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
      vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());
      // 预检响应到达之前，E1U1 已被别的入口占用
      vi.spyOn(API, "precheckReferenceVideoDuration").mockImplementation(async () => {
        act(() => {
          useTasksStore.setState({ tasks: [runningTask("E1U1")] as never });
        });
        return {
          needs_confirmation: true,
          script_duration: 3,
          request_duration: 4,
          adjustment: "up" as const,
        };
      });

      render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
      const batch = await screen.findByRole("button", { name: /Batch generate videos|批量生成视频/ });
      await waitFor(() => expect(batch).not.toBeDisabled());
      fireEvent.click(batch);

      const confirm = await screen.findByRole("button", { name: CONFIRM_CTA });
      // 被占用的 E1U1 不在清单里（unit_id 在画布单元列表里也出现，故限定弹窗作用域）
      const dialog = within(screen.getByRole("dialog"));
      expect(dialog.queryByText("E1U1")).not.toBeInTheDocument();
      expect(dialog.getByText("E1U2")).toBeInTheDocument();
      expect(dialog.getByText("E1U3")).toBeInTheDocument();

      fireEvent.click(confirm);
      await waitFor(() => expect(genSpy).toHaveBeenCalledTimes(2));
      expect(genSpy).not.toHaveBeenCalledWith("proj", 1, "E1U1");
    });

    // 折叠计数会藏起被折叠单元的调整方向：其中可能有成片更短的单元，而用户是在看不到
    // 它的情况下为它拍板。列表改为滚动展示全部。
    it("批量确认列出全部需确认单元，不折叠计数", async () => {
      const many = Array.from({ length: 8 }, (_, i) => mkUnit(`E1U${i + 1}`));
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: many });
      vi.spyOn(API, "generateReferenceVideoUnit").mockResolvedValue({
        task_id: "t9",
        deduped: false,
      } as never);
      stubPrecheck({
        needs_confirmation: true,
        script_duration: 3,
        request_duration: 4,
        adjustment: "up",
      });

      render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
      const batch = await screen.findByRole("button", { name: /Batch generate videos|批量生成视频/ });
      await waitFor(() => expect(batch).not.toBeDisabled());
      fireEvent.click(batch);

      await screen.findByRole("button", { name: CONFIRM_CTA });
      const dialog = within(screen.getByRole("dialog"));
      for (const unit of many) expect(dialog.getByText(unit.unit_id)).toBeInTheDocument();
    });

    it("预检失败的单元不入队并提示", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
      const genSpy = vi
        .spyOn(API, "generateReferenceVideoUnit")
        .mockResolvedValue({ task_id: "t1", deduped: false } as never);
      vi.spyOn(API, "precheckReferenceVideoDuration").mockRejectedValue(new Error("offline"));

      render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
      fireEvent.click(await screen.findByRole("button", { name: UNIT_GENERATE_CTA }));

      await waitFor(() => {
        expect(useAppStore.getState().toast?.text).toMatch(/时长核对失败|Could not check the length/);
      });
      expect(genSpy).not.toHaveBeenCalled();
    });
  });

  it("没有待生成 unit 时批量生成按钮禁用", async () => {
    // 唯一的 unit 已有成片：statusMap 为 ready，批量入口无作用对象
    const ready = mkUnit("E1U1");
    ready.generated_assets.video_clip = "videos/E1U1.mp4";
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [ready] });

    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);

    const batch = await screen.findByRole("button", { name: /Batch generate videos|批量生成视频/ });
    await waitFor(() => expect(batch).toBeDisabled());
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

  it("参考生视频分组失效信号自增时重拉分组，展示新落地的成片", async () => {
    // 生成完成的任务终态经项目事件 SSE 推来 → invalidateReferenceVideoUnits →
    // 本画布重拉分组，用户无需手动刷新即可看到成片。
    const listSpy = vi
      .spyOn(API, "listReferenceVideoUnits")
      .mockResolvedValue({ units: [mkUnit("E1U1")] });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(listSpy).toHaveBeenCalledTimes(1));

    act(() => {
      useAppStore.getState().invalidateReferenceVideoUnits();
    });

    await waitFor(() => expect(listSpy).toHaveBeenCalledTimes(2));
  });

  describe("存量声音过渡横幅", () => {
    const VOICE_UPDATED_AT = "2026-06-01T00:00:00+00:00";

    /** 一个已完成、且生成于当前声音设置之前的片段 */
    function staleUnit(): ReferenceVideoUnit {
      const u = mkUnit("E1U1");
      u.references = [{ type: "character", name: "王" }];
      u.generated_assets = { ...u.generated_assets, status: "completed", video_generated_at: null };
      return u;
    }

    function mountWithStaleClip() {
      useProjectsStore.setState({
        currentProjectName: "proj",
        currentProjectData: {
          ...STUB_PROJECT,
          characters: { 王: { description: "", voice_updated_at: VOICE_UPDATED_AT } },
        },
        refreshProject: vi.fn().mockResolvedValue({ status: "ok" }),
      } as never);
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [staleUnit()] });
      render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    }

    it("关闭时写回角色自己的 voice_updated_at，而不是本机当前时间", async () => {
      // 客户端时钟可能落后于服务端；写本机时间会让「关闭后不再出现」在偏差下永久失效。
      const patch = vi.spyOn(API, "updateCharacter").mockResolvedValue({} as never);
      mountWithStaleClip();

      const dismiss = await screen.findByRole("button", { name: /Got it|知道了/ });
      fireEvent.click(dismiss);

      await waitFor(() =>
        expect(patch).toHaveBeenCalledWith("proj", "王", { voice_notice_dismissed_at: VOICE_UPDATED_AT }),
      );
    });

    it("关闭请求失败时提示用户，不静默留下未关闭的横幅", async () => {
      vi.spyOn(API, "updateCharacter").mockRejectedValue(new Error("boom"));
      mountWithStaleClip();

      const dismiss = await screen.findByRole("button", { name: /Got it|知道了/ });
      fireEvent.click(dismiss);

      await waitFor(() => expect(useAppStore.getState().toast?.tone).toBe("error"));
    });

    it("PATCH 成功但 refreshProject 失败时仍提示用户，不静默吞掉", async () => {
      // refreshProject 失败时 resolve "failed" 而非 reject——只 await 不检查结果/传
      // onError 会让这次失败在 UI 上完全无感知。
      vi.spyOn(API, "updateCharacter").mockResolvedValue({} as never);
      const refreshProject = vi.fn(
        (_name: string, options?: { onError?: (err: unknown) => void }): Promise<string> => {
          options?.onError?.(new Error("network down"));
          return Promise.resolve("failed");
        },
      );
      useProjectsStore.setState({
        currentProjectName: "proj",
        currentProjectData: {
          ...STUB_PROJECT,
          characters: { 王: { description: "", voice_updated_at: VOICE_UPDATED_AT } },
        },
        refreshProject,
      } as never);
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [staleUnit()] });
      render(<ReferenceVideoCanvas projectName="proj" episode={1} />);

      const dismiss = await screen.findByRole("button", { name: /Got it|知道了/ });
      fireEvent.click(dismiss);

      await waitFor(() => expect(useAppStore.getState().toast?.tone).toBe("error"));
    });
  });
});
