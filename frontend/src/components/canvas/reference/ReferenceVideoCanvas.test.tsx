import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, act, within } from "@testing-library/react";
import { ReferenceVideoCanvas } from "./ReferenceVideoCanvas";
import { useReferenceVideoStore, referenceVideoCacheKey } from "@/stores/reference-video-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useActiveResourceIds, useLatestTasksByResource, useTasksStore } from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";
import { useCostStore } from "@/stores/cost-store";
import { API } from "@/api";
import type { ReferenceDurationPrecheck, ReferenceVideoUnit } from "@/types";
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

function mkUnit(id: string, text = "x"): ReferenceVideoUnit {
  return {
    unit_id: id,
    text,
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

/** 批量端点的准入结论骨架；恒 200，decision 携带结局。 */
function mkAdmission(patch: Record<string, unknown> = {}) {
  return {
    decision: "admitted",
    operation: "generate_reference_videos_batch",
    selection: "explicit",
    narration_delivery: "post_production",
    units: [],
    confirmation: null,
    skipped_unit_ids: [],
    task_ids: ["t9"],
    task_ids_by_unit: { E1U1: "t9" },
    enqueue_failures: [],
    deduped: false,
    ...patch,
  } as never;
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
    // 时长取档预检默认「与请求时长基准一致」：生成入口无需确认。
    vi.spyOn(API, "precheckReferenceVideoDuration").mockResolvedValue({
      needs_confirmation: false,
      script_duration: 3,
      duration_input: 3,
      request_duration: 3,
      adjustment: "exact",
      declared_capability: "i2v",
      hydrated_capability: "i2v",
      provider_id: "kling",
      model_id: "kling-v2-1-master",
      problems: [],
    });
  });
  afterEach(() => vi.restoreAllMocks());

  it("loads units on mount and renders the list", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1"), mkUnit("E1U2")] });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
    expect(screen.getByTestId("unit-row-E1U2")).toBeInTheDocument();
  });

  it("keeps request controls outside the tablist semantics", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);

    await screen.findByTestId("unit-row-E1U1");
    const tablist = screen.getByRole("tablist", {
      name: /Workspace main tabs|工作台主面板切换|Tab chính của workspace/,
    });
    const delivery = screen.getByRole("group", { name: /Narration delivery|旁白交付/ });
    expect(within(tablist).getAllByRole("tab")).toHaveLength(2);
    expect(tablist).not.toContainElement(delivery);
  });

  it("auto-selects first unit on load and shows preview generate button", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generate video|生成视频/ })).toBeInTheDocument();
    });
  });

  // 解析预览与文稿共用编辑器列：切到解析视图时 textarea 让位给只读派生视图
  it("switches the editor column between the script and its parse preview", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1", "中景。")],
    });
    const previewSpy = vi.spyOn(API, "previewReferenceScript").mockResolvedValue({
      utterances: [],
      warnings: [{ key: "ref_warn_unregistered_mention", message: "@[王五] 未在角色/场景/道具中登记" }],
    });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);

    await screen.findByRole("combobox");
    fireEvent.click(await screen.findByRole("tab", { name: /Parse preview|解析预览/ }));

    expect(screen.queryByRole("combobox")).toBeNull();
    await waitFor(() => expect(previewSpy).toHaveBeenCalledWith("proj", 1, "中景。", expect.anything()));
    expect(await screen.findByText("@[王五] 未在角色/场景/道具中登记")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /^(Script|文稿)$/ }));
    expect(await screen.findByRole("combobox")).toBeInTheDocument();
  });

  // 两个 tabpanel 同时刻只挂载一个，共用静态 id 会让未选中 tab 的 aria-controls
  // 指向当前激活面板——而该面板的 aria-labelledby 归属对方 tab，读屏播报错位。
  it("points each editor-view tab at its own panel", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1", "中景。")],
    });
    vi.spyOn(API, "previewReferenceScript").mockResolvedValue({
      utterances: [],
      warnings: [],
    });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);

    const scriptTab = await screen.findByRole("tab", { name: /^(Script|文稿)$/ });
    const parseTab = screen.getByRole("tab", { name: /Parse preview|解析预览/ });
    const scriptControls = scriptTab.getAttribute("aria-controls");
    const parseControls = parseTab.getAttribute("aria-controls");
    expect(scriptControls).not.toBe(parseControls);

    // 每个 tab 指向的面板，其 aria-labelledby 必须指回该 tab 自身
    const scriptPanel = screen.getByRole("tabpanel");
    expect(scriptPanel.id).toBe(scriptControls);
    expect(scriptPanel.getAttribute("aria-labelledby")).toBe(scriptTab.id);

    fireEvent.click(parseTab);
    const parsePanel = await screen.findByRole("tabpanel");
    expect(parsePanel.id).toBe(parseControls);
    expect(parsePanel.getAttribute("aria-labelledby")).toBe(parseTab.id);
    // 解析预览只读、无可聚焦后代：面板自身须能接焦点，否则键盘用户翻不到折线以下的内容
    expect(parsePanel).toHaveAttribute("tabindex", "0");
  });

  // `out["__proto__"] = kind` 在普通对象上走继承的 setter、不落自有属性，
  // 登记过的 `__proto__` 角色会在高亮里显示成未登记（后端照常解析）
  it("resolves an asset named __proto__ in the highlight lookup", async () => {
    useProjectsStore.setState({
      currentProjectName: "proj",
      currentProjectData: {
        ...STUB_PROJECT,
        // 计算键：字面量里的 `__proto__:` 是设原型的特例，不会建自有属性
        characters: { ["__proto__"]: { description: "" } },
      } as ProjectData,
    });
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1", "@[__proto__] 出场。")],
    });
    vi.spyOn(API, "previewReferenceScript").mockResolvedValue({
      utterances: [],
      warnings: [],
    });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);

    await screen.findByRole("combobox");
    fireEvent.click(await screen.findByRole("tab", { name: /Parse preview|解析预览/ }));

    // 只看解析预览面板内的高亮，避开单元列表卡片里的同名文本
    const panel = await screen.findByRole("tabpanel");
    const mention = (await within(panel).findAllByText(/__proto__/)).find((el) =>
      el.className.includes("sky"),
    );
    expect(mention).toBeDefined();
  });

  it("renders the ReferenceVideoCard textarea once auto-selected", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1")],
    });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    const ta = await screen.findByRole("combobox");
    expect((ta as HTMLTextAreaElement).value).toContain("x");
  });

  it("offers explicit generate/regenerate/listen actions for unit-owned narration TTS", async () => {
    const unit = mkUnit("E1U1");
    unit.text = "镜头推进。\n{夜色深沉。}";
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit] });
    useProjectsStore.setState({
      currentProjectName: "proj",
      currentProjectData: {
        ...STUB_PROJECT,
        episodes: [{ episode: 1, title: "", script_file: "episode_1.json" }],
      },
    });
    const generate = vi
      .spyOn(API, "generateNarrationAudio")
      .mockResolvedValue({ success: true, task_id: "tts-1", deduped: false, message: "queued" });

    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    fireEvent.click(await screen.findByRole("button", { name: /生成旁白配音|Generate narration audio/ }));

    await waitFor(() =>
      expect(generate).toHaveBeenCalledWith("proj", "E1U1", "episode_1.json"),
    );

    unit.generated_assets.narration_audio = "audio/segment_E1U1.wav";
    useReferenceVideoStore.setState({
      unitsByEpisode: { [referenceVideoCacheKey("proj", 1)]: [unit] },
    } as never);
    expect(await screen.findByRole("button", { name: /重新生成旁白配音|Regenerate narration audio/ })).toBeInTheDocument();
    expect(document.querySelector('audio[src*="audio/segment_E1U1.wav"]')).not.toBeNull();
  });

  // 正文是单一真相：保存把编辑器里那段文本原样送到 PATCH 的 prompt 位，
  // 请求里不带任何独立参考图字段。
  it("saves an edited body as the unit's text, with nothing else in the request", async () => {
    const unit = mkUnit("E1U1", "推门。");
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit] });
    const patchSpy = vi
      .spyOn(API, "patchReferenceVideoUnit")
      .mockResolvedValue({ unit: { ...unit, text: "@[张三] 推门而入。" } });

    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    const ta = await screen.findByRole("combobox");
    fireEvent.change(ta, { target: { value: "@[张三] 推门而入。" } });

    fireEvent.click(await screen.findByRole("button", { name: /^(Save|保存)$/ }));
    await waitFor(() =>
      expect(patchSpy).toHaveBeenCalledWith("proj", 1, "E1U1", { prompt: "@[张三] 推门而入。" }),
    );
  });

  // 未登记的 `@[名称]` 只是提示：保存与生成入口都不受影响。
  it("keeps saving and generating available when the body mentions an unregistered name", async () => {
    const unit = mkUnit("E1U1", "推门。");
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit] });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    const ta = await screen.findByRole("combobox");
    fireEvent.change(ta, { target: { value: "@[查无此人] 推门而入。" } });

    expect(await screen.findByRole("button", { name: /^(Save|保存)$/ })).not.toBeDisabled();
    for (const btn of screen.getAllByRole("button", { name: /Generate video|生成视频/ })) {
      expect(btn).not.toBeDisabled();
    }
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

  it("commits a free-form duration once after editing instead of patching intermediate digits", async () => {
    const unit = mkUnit("E1U1");
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit] });
    const patchSpy = vi
      .spyOn(API, "patchReferenceVideoUnit")
      .mockResolvedValue({ unit: { ...unit, duration_seconds: 120 } });

    render(<ReferenceVideoCanvas projectName="proj" episode={1} freeDuration />);
    const input = await screen.findByRole("spinbutton", { name: /Duration|时长/ });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "1" } });
    fireEvent.change(input, { target: { value: "12" } });
    fireEvent.change(input, { target: { value: "120" } });

    expect(patchSpy).not.toHaveBeenCalled();
    fireEvent.blur(input);

    await waitFor(() =>
      expect(patchSpy).toHaveBeenCalledWith("proj", 1, "E1U1", { duration_seconds: 120 }),
    );
    expect(patchSpy).toHaveBeenCalledTimes(1);
  });

  it("protects an uncommitted free-form duration from tab unload", async () => {
    const unit = mkUnit("E1U1");
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit] });
    const patchSpy = vi.spyOn(API, "patchReferenceVideoUnit");

    render(<ReferenceVideoCanvas projectName="proj" episode={1} freeDuration />);
    const input = await screen.findByRole("spinbutton", { name: /Duration|时长/ });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "12" } });

    await waitFor(() => {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(true);
    });
    expect(patchSpy).not.toHaveBeenCalled();
  });

  it("lets an explicit same-value duration confirm a duration-only replan marker", async () => {
    const unit = { ...mkUnit("E1U1"), needs_replan: true };
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit] });
    const patchSpy = vi
      .spyOn(API, "patchReferenceVideoUnit")
      .mockResolvedValue({ unit: { ...unit, needs_replan: false } });

    render(<ReferenceVideoCanvas projectName="proj" episode={1} freeDuration />);
    const input = await screen.findByRole("spinbutton", { name: /Duration|时长/ });
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.change(input, { target: { value: "3" } });
    fireEvent.blur(input);

    await waitFor(() =>
      expect(patchSpy).toHaveBeenCalledWith("proj", 1, "E1U1", { duration_seconds: 3 }),
    );
  });

  // 参考生视频按申请秒数计价：改档位即改估价。SSE 会让分组缓存最终一致，费用面板仍由
  // 本地写成功主动刷新，避免当前浏览器等待事件回环才显示新估价。
  it("refreshes cost estimates after a duration patch succeeds", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    vi.spyOn(API, "patchReferenceVideoUnit").mockResolvedValue({
      unit: { ...mkUnit("E1U1"), duration_seconds: 8 },
    });
    const fetchSpy = vi.spyOn(useCostStore.getState(), "debouncedFetch").mockImplementation(() => {});
    render(
      <ReferenceVideoCanvas projectName="proj" episode={1} durationOptions={[3, 8]} durationOptionsNoReference={[3, 8]} />,
    );
    const select = await screen.findByRole("combobox", { name: /Duration|时长/ });
    fireEvent.change(select, { target: { value: "8" } });
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledWith("proj"));
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

  // 参考图约束按 unit 生效：正文里没有资产提及的 unit 不应因同集内其它带图 unit 而被收窄。
  it("offers the no-reference tier set for a unit whose body mentions no asset", async () => {
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

  it("offers the reference-narrowed tier set for a unit whose body mentions an asset", async () => {
    useProjectsStore.setState({
      currentProjectName: "proj",
      currentProjectData: { ...STUB_PROJECT, characters: { 王: { description: "" } } },
    });
    const unit = mkUnit("E1U1", "@[王] 推门。");
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

  // 主 tab：视频单元 / 内容整理。默认 "视频单元"，即 UnitList 区域可见。
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

  it("switches main tab between units and content organization", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
    const unitsTab = screen.getByRole("tab", { name: /Video units|视频单元/ });
    const preprocTab = screen.getByRole("tab", { name: /Content Organization|内容整理/ });
    fireEvent.click(preprocTab);
    expect(preprocTab).toHaveAttribute("aria-selected", "true");
    expect(unitsTab).toHaveAttribute("aria-selected", "false");
    // 内容整理 tab 下 UnitList 不渲染
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

  // preproc 入口从二级页面跳转改为主 tab 切换；切到内容整理 tab 后 UnitList 被隐藏，
  // step1 按集预览面板（ReferenceStep1PreviewPanel）inline 渲染。
  it("inline-renders the step1 preview panel via the main tab", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1"), mkUnit("E1U2")],
    });
    vi.spyOn(API, "getScriptReview").mockResolvedValue({
      episode: 1,
      content_mode: "narration",
      status: "pending_review",
      fingerprint: "fp",
      confirmed_at: null,
      quarantine: null,
      supported_durations: null,
      duration_tiers: null,
      content: { units: [{ unit_id: "E1U1", text: "shot text", duration_seconds: 5, source_text: "" }] },
    });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
    const preprocTab = screen.getByRole("tab", { name: /Content Organization|内容整理/ });
    fireEvent.click(preprocTab);
    expect(preprocTab).toHaveAttribute("aria-selected", "true");
    // UnitList 被隐藏，改由预览面板渲染 step1 结构化中间态（只读高亮文稿）
    expect(screen.queryByTestId("unit-row-E1U1")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("shot text")).toBeInTheDocument());
  });

  // step2 剧本未生成时（仅 segmented）units 端点无脚本可拆、会 404：默认落 preproc tab
  // 且不发起 units 请求，避免用户先看到一个报错的 Unit 面板。
  it("defaults to preproc tab and skips loadUnits when hasScript is false", async () => {
    const listSpy = vi.spyOn(API, "listReferenceVideoUnits");
    vi.spyOn(API, "getScriptReview").mockResolvedValue({
      episode: 1,
      content_mode: "narration",
      status: "pending_review",
      fingerprint: "fp",
      confirmed_at: null,
      quarantine: null,
      supported_durations: null,
      duration_tiers: null,
      content: { units: [{ unit_id: "E1U1", text: "shot text", duration_seconds: 5, source_text: "" }] },
    });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} hasScript={false} />);
    const preprocTab = await screen.findByRole("tab", { name: /Content Organization|内容整理/ });
    expect(preprocTab).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(screen.getByText("shot text")).toBeInTheDocument());
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
      quarantine: null,
      supported_durations: null,
      duration_tiers: null,
      content: { units: [] },
    });
    const { rerender } = render(<ReferenceVideoCanvas projectName="proj" episode={1} hasScript={false} />);
    const preprocTab = await screen.findByRole("tab", { name: /Content Organization|内容整理/ });
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
    const btn = await screen.findByRole("button", { name: UNIT_GENERATE_CTA });
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
    // 失败任务行仍在，statusMap 未变；预览区不能同时叠出「生成中」占位与
    // 失败覆盖层——inFlight 展示应覆盖 failed 状态。
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

  // 批量入口的作用对象是「全部尚无成片的 unit」。在途任务与 needs_replan 是服务端准入要逐条
  // 报告、并据此让整批零任务入队的缺口：在浏览器里先摘掉，服务端只会看到健康子集并照常建任务。
  it("批量生成把在途与 needs_replan 的 unit 一并提交给服务端准入", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1"), mkUnit("E1U2"), { ...mkUnit("E1U3"), needs_replan: true }],
    });
    const batchSpy = vi
      .spyOn(API, "generateReferenceVideoBatch")
      .mockResolvedValue(mkAdmission());
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    const batch = await screen.findByRole("button", { name: /Batch generate videos|批量生成视频/ });
    await waitFor(() => expect(batch).not.toBeDisabled());

    // 渲染之后、点击之前，E1U1 已被别的入口占用
    act(() => {
      useTasksStore.setState({ tasks: [runningTask("E1U1")] as never });
    });

    fireEvent.click(batch);
    await waitFor(() => expect(batchSpy).toHaveBeenCalledTimes(1));
    expect(batchSpy).toHaveBeenCalledWith("proj", 1, {
      unit_ids: ["E1U1", "E1U2", "E1U3"],
      narration_delivery: "post_production",
    });
  });

  it("批量入口一次请求走服务端准入，admitted 时提示已入队", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1"), mkUnit("E1U2")],
    });
    const batchSpy = vi
      .spyOn(API, "generateReferenceVideoBatch")
      .mockResolvedValue(mkAdmission({
        task_ids: ["t1", "t2"],
        task_ids_by_unit: { E1U1: "t1", E1U2: "t2" },
      }));
    const unitSpy = vi.spyOn(API, "generateReferenceVideoUnit");

    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    const batch = await screen.findByRole("button", { name: /Batch generate videos|批量生成视频/ });
    await waitFor(() => expect(batch).not.toBeDisabled());
    fireEvent.click(batch);

    await waitFor(() =>
      expect(batchSpy).toHaveBeenCalledWith("proj", 1, {
        unit_ids: ["E1U1", "E1U2"],
        narration_delivery: "post_production",
      }),
    );
    // 不再逐个串行入队
    expect(unitSpy).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toMatch(/已提交 2 个视频生成任务|Queued 2 video/);
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  // 交付方式是本次请求的一部分，批量与单元入口读同一个画布选择：批量不带上它，
  // 整批会按服务端默认的「后期配音」准入，用户选的「使用当前 TTS」被静默丢弃。
  it("批量入口带上本次的旁白交付选择", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    const batchSpy = vi
      .spyOn(API, "generateReferenceVideoBatch")
      .mockResolvedValue(mkAdmission());

    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    fireEvent.click(await screen.findByRole("button", { name: /Use current TTS|使用当前 TTS/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Batch generate videos|批量生成视频/ }));

    await waitFor(() => expect(batchSpy).toHaveBeenCalled());
    expect(batchSpy).toHaveBeenCalledWith(
      "proj",
      1,
      expect.objectContaining({ narration_delivery: "use_tts" }),
    );
  });

  it("批量入口未改选择时按后期配音提交", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    const batchSpy = vi
      .spyOn(API, "generateReferenceVideoBatch")
      .mockResolvedValue(mkAdmission());

    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    fireEvent.click(await screen.findByRole("button", { name: /Batch generate videos|批量生成视频/ }));

    await waitFor(() => expect(batchSpy).toHaveBeenCalled());
    expect(batchSpy).toHaveBeenCalledWith(
      "proj",
      1,
      expect.objectContaining({ narration_delivery: "post_production" }),
    );
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
    const batchSpy = vi
      .spyOn(API, "generateReferenceVideoBatch")
      .mockResolvedValue(mkAdmission());
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
    await waitFor(() => expect(batchSpy).toHaveBeenCalledTimes(1));
    expect(batchSpy).toHaveBeenCalledWith("proj", 1, {
      unit_ids: ["E1U2"],
      narration_delivery: "post_production",
    });
  });

  // 复核把目标清空时界面必须说一句：两条提交路径都以裸 return 结束的话，用户看到的是
  // 「点了没反应」，会以为任务已经提交。
  it("复核后没有可提交的目标时给出提示", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    vi.spyOn(API, "uploadReferenceUnitVideo").mockReturnValue(new Promise(() => {}) as never);
    const batchSpy = vi.spyOn(API, "generateReferenceVideoBatch");
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    const { container } = render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    const batch = await screen.findByRole("button", { name: /Batch generate videos|批量生成视频/ });
    await waitFor(() => expect(batch).not.toBeDisabled());

    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    fireEvent.change(input!, { target: { files: [new File(["x"], "clip.mp4", { type: "video/mp4" })] } });

    fireEvent.click(batch);

    await waitFor(() => expect(useAppStore.getState().toast?.text).toBeTruthy());
    expect(batchSpy).not.toHaveBeenCalled();
  });

  // 时长取档确认：模型只接受离散档位，请求时长基准落在档位之间时按能装下它的最小档位生成，
  // 成片不裁剪——秒数不一致这件事必须在入队前讲清楚，由用户决定是否继续。
  describe("时长取档确认", () => {
    const CONFIRM_CTA = /Generate at this length|按此时长生成/;

    function stubPrecheck(
      precheck: Pick<
        ReferenceDurationPrecheck,
        "needs_confirmation" | "script_duration" | "request_duration" | "adjustment"
      >,
    ) {
      vi.spyOn(API, "precheckReferenceVideoDuration").mockResolvedValue({
        ...precheck,
        duration_input: precheck.script_duration,
        declared_capability: "i2v",
        hydrated_capability: "i2v",
        provider_id: "kling",
        model_id: "kling-v2-1-master",
        problems: [],
      });
    }

    it("申请秒数与请求时长基准不一致时先确认，确认后才入队", async () => {
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
      // 弹窗内容含请求时长基准、申请秒数与「成片更长」的说明。
      expect(screen.getByText(/5 秒|5s/)).toBeInTheDocument();
      expect(screen.getByText(/^(?:8 秒|8s)$/)).toBeInTheDocument();
      expect(screen.getByText(/长 3 秒|3s longer/)).toBeInTheDocument();

      fireEvent.click(confirm);
      await waitFor(() =>
        expect(genSpy).toHaveBeenCalledWith("proj", 1, "E1U1", {
          confirmed_request_duration_seconds: 8,
        }),
      );
    });

    it("复用上游 TTS 时长选项完成预检、确认与入队", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
      const precheckSpy = vi.spyOn(API, "precheckReferenceVideoDuration").mockResolvedValue({
        needs_confirmation: true,
        script_duration: 3,
        duration_input: 9.5,
        request_duration: 12,
        adjustment: "up",
        declared_capability: "i2v",
        hydrated_capability: "i2v",
        provider_id: "kling",
        model_id: "kling-v2-1-master",
        problems: [],
      });
      const generateSpy = vi.spyOn(API, "generateReferenceVideoUnit").mockResolvedValue({
        task_id: "t1",
        deduped: false,
      } as never);
      const requestOptions = {
        narration_delivery: "use_tts" as const,
      };

      render(<ReferenceVideoCanvas projectName="proj" episode={1} requestOptions={requestOptions} />);
      fireEvent.click(await screen.findByRole("button", { name: UNIT_GENERATE_CTA }));

      await waitFor(() =>
        expect(precheckSpy).toHaveBeenCalledWith(
          "proj",
          1,
          "E1U1",
          expect.objectContaining(requestOptions),
        ),
      );
      const dialog = within(screen.getByRole("dialog"));
      expect(dialog.getByText(/9\.5 秒|9\.5s/)).toBeInTheDocument();
      expect(dialog.getByText(/^3 秒$|^3s$/)).toBeInTheDocument();

      fireEvent.click(await screen.findByRole("button", { name: CONFIRM_CTA }));
      await waitFor(() =>
        expect(generateSpy).toHaveBeenCalledWith("proj", 1, "E1U1", {
          ...requestOptions,
          confirmed_request_duration_seconds: 12,
        }),
      );
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
      expect(screen.getByText(/放不下完整的请求时长基准|cannot hold the full request basis/)).toBeInTheDocument();
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
      await waitFor(() =>
        expect(genSpy).toHaveBeenCalledWith("proj", 1, "E1U1", {
          confirmed_request_duration_seconds: 8,
        }),
      );
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

  // 批量的三种结局都是评估成功：admitted 已建任务，confirmation_required 与 blocked
  // 一个任务也没建，界面必须把「为什么没开始」讲全，而不是塌成一句通用错误。
  describe("整批准入判定", () => {
    const BATCH_CONFIRM_CTA = /Generate at these lengths|按这些档位生成/;

    async function clickBatch() {
      const batch = await screen.findByRole("button", { name: /Batch generate videos|批量生成视频/ });
      await waitFor(() => expect(batch).not.toBeDisabled());
      fireEvent.click(batch);
      return batch;
    }

    it("需确认时按档位聚合陈述，确认后带 confirmed_request_durations 重发", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2"), mkUnit("E1U3")],
      });
      const batchSpy = vi
        .spyOn(API, "generateReferenceVideoBatch")
        .mockResolvedValueOnce(
          mkAdmission({
            decision: "confirmation_required",
            task_ids: [],
            units: [
              { unit_id: "E1U1", admitted: false, request_duration_seconds: 8, problems: [] },
              { unit_id: "E1U2", admitted: false, request_duration_seconds: 8, problems: [] },
              { unit_id: "E1U3", admitted: false, request_duration_seconds: 4, problems: [] },
            ],
            confirmation: {
              tiers: [
                {
                  request_duration_seconds: 8,
                  unit_count: 2,
                  unit_ids: ["E1U1", "E1U2"],
                  cost_amount: 1.6,
                  cost_currency: "USD",
                },
                {
                  request_duration_seconds: 4,
                  unit_count: 1,
                  unit_ids: ["E1U3"],
                  cost_amount: null,
                  cost_currency: null,
                },
              ],
            },
          }),
        )
        .mockResolvedValueOnce(mkAdmission({ task_ids: ["t1", "t2", "t3"] }));

      render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
      await clickBatch();

      const confirm = await screen.findByRole("button", { name: BATCH_CONFIRM_CTA });
      const dialog = within(screen.getByRole("dialog"));
      // 档位分组：秒数 × 单元数 + 合计费用；报价不全的档位不展示假合计
      expect(dialog.getByText(/^(?:8 秒|8s)$/)).toBeInTheDocument();
      expect(dialog.getByText(/2 个单元|2 units/)).toBeInTheDocument();
      expect(dialog.getByText(/约 \$1\.60|about \$1\.60/)).toBeInTheDocument();
      expect(dialog.getByText(/报价不可用|price unavailable/)).toBeInTheDocument();
      for (const unitId of ["E1U1", "E1U2", "E1U3"]) {
        expect(dialog.getByText(unitId)).toBeInTheDocument();
      }

      fireEvent.click(confirm);
      await waitFor(() => expect(batchSpy).toHaveBeenCalledTimes(2));
      expect(batchSpy).toHaveBeenLastCalledWith("proj", 1, {
        unit_ids: ["E1U1", "E1U2", "E1U3"],
        narration_delivery: "post_production",
        confirmed_request_durations: { E1U1: 8, E1U2: 8, E1U3: 4 },
      });
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });

    // 弹窗停留时长由用户决定，可以很长：其间别处完成的单元若按冻结清单原样重发，队列
    // 去重（只看 queued/running/cancelling）拦不住，会重跑一次生成、重复计费并覆盖成片。
    it("确认停留期间已完成的单元不再重发", async () => {
      const [u1, u2] = [mkUnit("E1U1"), mkUnit("E1U2")];
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [u1, u2] });
      const batchSpy = vi
        .spyOn(API, "generateReferenceVideoBatch")
        .mockResolvedValueOnce(
          mkAdmission({
            decision: "confirmation_required",
            task_ids: [],
            units: [
              { unit_id: "E1U1", admitted: false, request_duration_seconds: 4, problems: [] },
              { unit_id: "E1U2", admitted: false, request_duration_seconds: 4, problems: [] },
            ],
            confirmation: {
              tiers: [
                {
                  request_duration_seconds: 4,
                  unit_count: 2,
                  unit_ids: ["E1U1", "E1U2"],
                  cost_amount: 0.8,
                  cost_currency: "USD",
                },
              ],
            },
          }),
        )
        .mockResolvedValueOnce(mkAdmission());

      render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
      await clickBatch();
      const confirm = await screen.findByRole("button", { name: BATCH_CONFIRM_CTA });

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
      await waitFor(() => expect(batchSpy).toHaveBeenCalledTimes(2));
      expect(batchSpy).toHaveBeenLastCalledWith("proj", 1, {
        unit_ids: ["E1U2"],
        narration_delivery: "post_production",
        confirmed_request_durations: { E1U2: 4 },
      });
    });

    it("受阻时列出每个单元的缺口与下一步，并标明被连带扣下的单元", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2"), mkUnit("E1U3")],
      });
      const batchSpy = vi.spyOn(API, "generateReferenceVideoBatch").mockResolvedValue(
        mkAdmission({
          decision: "blocked",
          task_ids: [],
          skipped_unit_ids: [],
          units: [
            // action 用 GenerationAction 的取值：服务端已把各上游模块的动作词归一到该集合，
            // 界面的下一步文案按这套词汇取键。
            {
              unit_id: "E1U1",
              admitted: false,
              problems: [
                {
                  code: "reference_asset_missing",
                  action: "generate_dependency",
                  message: "引用的角色图缺失",
                  params: {},
                },
              ],
            },
            {
              unit_id: "E1U2",
              admitted: false,
              current_duration_seconds: 5,
              request_duration_seconds: 12,
              problems: [
                {
                  code: "needs_replan",
                  action: "replan_unit",
                  message: "该单元需要重新规划",
                  params: {},
                },
              ],
            },
            {
              // 服务端对自身通过、被同批别的 unit 连带扣下的单元发的就是这个形状：
              // admitted 保持它自己的判定，withheld 说明它为什么没被提交。
              unit_id: "E1U3",
              admitted: true,
              withheld: true,
              problems: [
                {
                  code: "generation_batch_admission_withheld",
                  action: "retry",
                  message: "其他单元受阻，本单元一并未提交",
                  params: { blocked_unit_ids: ["E1U1", "E1U2"] },
                },
              ],
            },
          ],
        }),
      );

      render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
      await clickBatch();

      const dialog = within(await screen.findByRole("dialog"));
      // 全部缺口都在，不塌成第一条
      expect(dialog.getByText("引用的角色图缺失")).toBeInTheDocument();
      expect(dialog.getByText("该单元需要重新规划")).toBeInTheDocument();
      expect(dialog.getByText(/补上或更换缺失的参考素材|add or replace the missing reference/)).toBeInTheDocument();
      expect(dialog.getByText(/改写这个单元|rewrite this unit/)).toBeInTheDocument();
      // 当前/所需档位与缺口同列，用户才看得出差多少
      expect(dialog.getByText(/当前 5 秒 · 申请 12 秒|now 5s · requesting 12s/)).toBeInTheDocument();
      // 被连带扣下的单元单列，说明它自身没问题
      expect(dialog.getByText(/本身没问题|w(as|ere) fine but w(as|ere) held back/)).toBeInTheDocument();
      for (const unitId of ["E1U1", "E1U2", "E1U3"]) {
        expect(dialog.getByText(unitId)).toBeInTheDocument();
      }

      // 受阻是终局：关闭不重发
      fireEvent.click(dialog.getByRole("button", { name: /Got it|知道了/ }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(batchSpy).toHaveBeenCalledTimes(1);
    });

    it("批量请求失败时提示错误且不留下结论面板", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
      vi.spyOn(API, "generateReferenceVideoBatch").mockRejectedValue(new Error("offline"));

      render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
      await clickBatch();

      await waitFor(() => {
        expect(useAppStore.getState().toast?.text).toMatch(/批量生成请求失败|Batch generation request failed/);
      });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
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

    /** 一个已完成、且生成于当前声音设置之前的片段；角色须开口说话，音色才作用于它 */
    function staleUnit(): ReferenceVideoUnit {
      const u = mkUnit("E1U1", "@[王] 推门。@[王]{我来了。}");
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
