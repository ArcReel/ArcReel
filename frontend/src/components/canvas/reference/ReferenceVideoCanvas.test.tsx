import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render as rtlRender, screen, waitFor, fireEvent, act, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { Router, useLocation } from "wouter";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { ReferenceVideoCanvas } from "./ReferenceVideoCanvas";
import { useReferenceVideoStore, referenceVideoCacheKey } from "@/stores/reference-video-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useActiveResourceIds, useLatestTasksByResource, useTasksStore } from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";
import { useCostStore } from "@/stores/cost-store";
import { API } from "@/api";
import { makeReferenceUnitCapability } from "@/test/factories";
import type {
  ReferenceDurationPrecheck,
  ReferenceUnitCapabilityMap,
  ReferenceVideoUnit,
  UnitGeneratedAssets,
} from "@/types";
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

function mkUnit(id: string, text = "x"): ReferenceVideoUnit & { generated_assets: UnitGeneratedAssets } {
  return {
    unit_id: id,
    text,
    duration_seconds: 3,
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

/** 服务端逐单元定桶结论：默认「无参考图 → i2v，档位 3/8」，用例按需覆盖。 */

/** 批量端点的准入结论骨架；恒 200，decision 携带结局。 */
function mkAdmission(patch: Record<string, unknown> = {}) {
  return {
    decision: "admitted",
    operation: "generate_reference_videos_batch",
    selection: "explicit",
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

// 单元预览面板的生成 CTA。锚定行首把批量入口「补齐视频」排除在外——两者都含
// 「生成视频」，不锚定会按 DOM 顺序先匹配到批量按钮，测到的就不是这条提交路径。
const UNIT_GENERATE_CTA = /^(Generate video|生成视频)/;

/** 与应用根部一致：画布挂在离开拦截之下，未保存的修改由它拦截。 */
function render(ui: ReactElement) {
  return rtlRender(ui, { wrapper: LeaveGuardProvider });
}

/** 打开时长下拉，返回各选项的秒数。 */
async function openDurationOptions(): Promise<number[]> {
  await userEvent.click(await screen.findByRole("combobox", { name: /Duration|时长/ }));
  return (await screen.findAllByRole("option")).map((option) => Number.parseInt(option.textContent ?? "", 10));
}

/** 集页路由给画布的视图：缺省停在视频单元视图。 */
const BOARD = { view: "board", onViewChange: () => {} } as const;

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
    vi.spyOn(API, "listEpisodeDrafts").mockResolvedValue({ episode: 1, drafts: [] });
  });
  afterEach(() => vi.restoreAllMocks());

  it("presents a prompt authoring draft needing fixes in place of the unit workbench and saves a hand fix", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    vi.spyOn(API, "listEpisodeDrafts").mockResolvedValue({
      episode: 1,
      drafts: [{ doc_type: "reference_prompt_authoring", editable_by: "user", violation_count: 1 }],
    });
    vi.spyOn(API, "getEpisodeDraft").mockResolvedValue({
      episode: 1,
      doc_type: "reference_prompt_authoring",
      revision: "rev-1",
      editable_by: "user",
      content: { title: "第1集", units: [{ text: "镜头一" }, { text: "镜头二｛全角｝" }] },
      violations: [{ code: "fullwidth_braces", label: "unit E1U2", message: "unit E1U2 使用了全角花括号", line: 0, item_index: 1 }],
      soft_violations: [],
      formal_exists: true,
      item_ids: ["E1U1", "E1U2"],
    });
    const save = vi
      .spyOn(API, "saveEpisodeDraft")
      .mockResolvedValue({ episode: 1, doc_type: "reference_prompt_authoring", adopted: true, draft: null });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

    expect(await screen.findByText("unit U2 使用了全角花括号")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "U2 · 1" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "U2 正文" }), { target: { value: "镜头二" } });
    fireEvent.click(screen.getByRole("button", { name: /保存并校验/ }));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith(
        "proj",
        1,
        "reference_prompt_authoring",
        { title: "第1集", units: [{ text: "镜头一" }, { text: "镜头二" }] },
        "rev-1",
      ),
    );
  });

  it("loads units on mount and renders the list", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
    expect(screen.getByTestId("unit-row-E1U2")).toBeInTheDocument();
  });

  it("renders units that carry no generated_assets section at all", async () => {
    // 后端只在生成时写入这一节：尚未生成过任何产物的单元没有它，读侧一律按可能缺席处理。
    const bare: ReferenceVideoUnit = mkUnit("E1U1");
    delete bare.generated_assets;
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [bare, mkUnit("E1U2")], unit_capabilities: {} });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
    expect(screen.getByTestId("unit-row-E1U2")).toBeInTheDocument();
  });

  it("removes the selected unit only after the danger confirmation", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    const deleteSpy = vi.spyOn(API, "deleteReferenceVideoUnit").mockResolvedValue(undefined);
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    await screen.findByTestId("unit-row-E1U1");

    fireEvent.click(await screen.findByRole("button", { name: /^(Remove unit|移除单元)$/ }));
    const dialog = screen.getByRole("alertdialog");
    expect(within(dialog).getByText(/U1/)).toBeInTheDocument();
    expect(deleteSpy).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: /^(Remove unit|移除单元)$/ }));

    await waitFor(() => expect(deleteSpy).toHaveBeenCalledWith("proj", 1, "E1U1"));
    await waitFor(() => expect(screen.queryByTestId("unit-row-E1U1")).not.toBeInTheDocument());
    expect(screen.getByTestId("unit-row-E1U2")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("does not remove a unit that became busy after the confirmation opened", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    const deleteSpy = vi.spyOn(API, "deleteReferenceVideoUnit").mockResolvedValue(undefined);
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    fireEvent.click(await screen.findByRole("button", { name: /^(Remove unit|移除单元)$/ }));

    act(() => {
      useTasksStore.setState({ tasks: [runningTask("E1U1")] as never });
    });
    const confirm = within(screen.getByRole("alertdialog")).getByRole("button", { name: /^(Remove unit|移除单元)$/ });
    await waitFor(() => expect(confirm).toBeDisabled());
    fireEvent.click(confirm);

    expect(deleteSpy).not.toHaveBeenCalled();
  });

  it("blocks removing a unit while its narration audio is being generated", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    useTasksStore.setState({ tasks: [{ ...runningTask("E1U1"), task_type: "tts" }] as never });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

    await screen.findByTestId("unit-row-E1U1");
    expect(screen.getByRole("button", { name: /^(Remove unit|移除单元)$/ })).toBeDisabled();
  });

  it("drops the removed unit's unsaved draft so it neither blocks unload nor resurfaces on a reused id", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1", "server text")], unit_capabilities: {} });
    vi.spyOn(API, "deleteReferenceVideoUnit").mockResolvedValue(undefined);
    vi.spyOn(API, "addReferenceVideoUnit").mockResolvedValue({
      unit: mkUnit("E1U1", "fresh unit"),
      // 无档位结论：只出现正文编辑器这一个 combobox，下方按角色查询才不会歧义。
      unit_capability: makeReferenceUnitCapability("E1U1", { allowed_durations: null }),
    });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    const textarea = (await screen.findByRole("combobox")) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "unsaved edit" } });

    fireEvent.click(screen.getByRole("button", { name: /^(Remove unit|移除单元)$/ }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: /^(Remove unit|移除单元)$/ }));
    await waitFor(() => expect(screen.queryByTestId("unit-row-E1U1")).not.toBeInTheDocument());

    await waitFor(() => {
      const unload = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(unload);
      expect(unload.defaultPrevented).toBe(false);
    });

    fireEvent.click(screen.getByRole("button", { name: /Add video unit|新增视频单元/ }));
    await waitFor(() => expect((screen.getByRole("combobox") as HTMLTextAreaElement).value).toContain("fresh unit"));
  });

  it("auto-selects first unit on load and shows preview generate button", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generate video|生成视频/ })).toBeInTheDocument();
    });
  });

  // 解析预览与文稿共用编辑器列：切到解析视图时 textarea 让位给只读派生视图
  it("switches the editor column between the script and its parse preview", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1", "中景。")], unit_capabilities: {} });
    const previewSpy = vi.spyOn(API, "previewReferenceScript").mockResolvedValue({
      utterances: [],
      warnings: [{ key: "ref_warn_unregistered_mention", message: "@[王五] 未在角色/场景/道具中登记" }],
    });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

    await screen.findByRole("combobox");
    fireEvent.click(await screen.findByRole("tab", { name: /Parse preview|解析预览/ }));

    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    await waitFor(() => expect(previewSpy).toHaveBeenCalledWith("proj", 1, "中景。", expect.anything()));
    expect(await screen.findByText("@[王五] 未在角色/场景/道具中登记")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /^(Script|文稿)$/ }));
    expect(await screen.findByRole("combobox")).toBeInTheDocument();
  });

  // 解析预览只读、没有可聚焦的后代：面板自身须能接焦点，键盘用户才翻得到折线以下的内容
  it("lets the keyboard reach the read-only parse preview", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1", "中景。")], unit_capabilities: {} });
    vi.spyOn(API, "previewReferenceScript").mockResolvedValue({
      utterances: [],
      warnings: [],
    });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

    fireEvent.click(await screen.findByRole("tab", { name: /Parse preview|解析预览/ }));
    const parsePanel = await screen.findByRole("tabpanel", { name: /Parse preview|解析预览/ });
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
      units: [mkUnit("E1U1", "@[__proto__] 出场。")], unit_capabilities: {} });
    vi.spyOn(API, "previewReferenceScript").mockResolvedValue({
      utterances: [],
      warnings: [],
    });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

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
      units: [mkUnit("E1U1")], unit_capabilities: {} });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    const ta = await screen.findByRole("combobox");
    expect((ta as HTMLTextAreaElement).value).toContain("x");
  });

  it("offers explicit generate/regenerate/listen actions for unit-owned narration TTS", async () => {
    const unit = mkUnit("E1U1");
    unit.text = "镜头推进。\n{夜色深沉。}";
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit], unit_capabilities: {} });
    useProjectsStore.setState({
      currentProjectName: "proj",
      currentProjectData: {
        ...STUB_PROJECT,
        narration_delivery: "use_tts",
        episodes: [{ episode: 1, title: "", script_file: "episode_1.json" }],
      },
    });
    const generate = vi
      .spyOn(API, "generateNarrationAudio")
      .mockResolvedValue({ success: true, task_id: "tts-1", deduped: false, message: "queued" });

    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
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

  it("keeps existing narration audio playable but hides generation for post-production projects", async () => {
    const unit = mkUnit("E1U1");
    unit.text = "镜头推进。\n{夜色深沉。}";
    unit.generated_assets.narration_audio = "audio/segment_E1U1.wav";
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit], unit_capabilities: {} });
    useProjectsStore.setState({
      currentProjectName: "proj",
      currentProjectData: {
        ...STUB_PROJECT,
        narration_delivery: "post_production",
        episodes: [{ episode: 1, title: "", script_file: "episode_1.json" }],
      },
    });

    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

    await waitFor(() => expect(document.querySelector('audio[src*="audio/segment_E1U1.wav"]')).not.toBeNull());
    expect(screen.queryByRole("button", { name: /生成旁白配音|Generate narration audio/ })).not.toBeInTheDocument();
  });

  // 正文是单一真相：保存把编辑器里那段文本原样送到 PATCH 的 prompt 位，
  // 请求里不带任何独立参考图字段。
  it("saves an edited body as the unit's text, with nothing else in the request", async () => {
    const unit = mkUnit("E1U1", "推门。");
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit], unit_capabilities: {} });
    const patchSpy = vi
      .spyOn(API, "patchReferenceVideoUnit")
      .mockResolvedValue({ unit: { ...unit, text: "@[张三] 推门而入。" }, unit_capability: makeReferenceUnitCapability("E1U1") });

    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    const ta = await screen.findByRole("combobox");
    fireEvent.change(ta, { target: { value: "@[张三] 推门而入。" } });

    fireEvent.click(await screen.findByRole("button", { name: /^(Save|保存)$/ }));
    await waitFor(() =>
      expect(patchSpy).toHaveBeenCalledWith("proj", 1, "E1U1", { prompt: "@[张三] 推门而入。" }),
    );
  });

  it("图标栏展开的完整列表里也能用键盘排序", async () => {
    // jsdom 不做布局：按条目在列表里的位置给出纵向排开的矩形，键盘拖动才有落点可算。
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const item = this.closest("li");
      const index = item ? Array.from(item.parentElement?.children ?? []).indexOf(item) : 0;
      return DOMRect.fromRect({ x: 0, y: index * 60, width: 300, height: 60 });
    });
    const units = [mkUnit("E1U1"), mkUnit("E1U2"), mkUnit("E1U3")];
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units, unit_capabilities: {} });
    const moveSpy = vi
      .spyOn(API, "moveReferenceVideoUnit")
      .mockResolvedValue({ units: [units[1], units[0], units[2]] });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

    fireEvent.click(await screen.findByRole("button", { name: /Expand list|展开列表/ }));
    const sheet = within(await screen.findByRole("dialog"));
    sheet.getByRole("button", { name: /U2/, description: /空格|space/i }).focus();
    await userEvent.keyboard(" ");
    await waitFor(() => expect(screen.getByText(/已拿起「U2」|picked up/i)).toBeInTheDocument());
    // 弹层会拦下方向键的冒泡；焦点在把手上时须放行给排序
    await userEvent.keyboard("{ArrowUp}");
    await waitFor(() => expect(screen.getByText(/「U2」移到第 1 项|moved to position 1/i)).toBeInTheDocument());
    await userEvent.keyboard(" ");

    await waitFor(() => expect(moveSpy).toHaveBeenCalledWith("proj", 1, "E1U2", null));
  });

  describe("正文编辑单元", () => {
    /** 在离开拦截之下导航：`base` 模拟项目路由的嵌套前缀。 */
    let navigate: (to: string) => void = () => {};
    function Navigator() {
      [, navigate] = useLocation();
      return null;
    }
    function renderAt(path: string, base = "") {
      window.history.replaceState(null, "", `${base}${path}`);
      return rtlRender(
        <LeaveGuardProvider>
          <Router base={base}>
            <Navigator />
            <ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />
          </Router>
        </LeaveGuardProvider>,
      );
    }
    afterEach(() => window.history.replaceState(null, "", "/"));

    it("「保存并生成」先保存正文，再为该单元入队", async () => {
      const unit = mkUnit("E1U1", "推门。");
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit], unit_capabilities: {} });
      const patchSpy = vi
        .spyOn(API, "patchReferenceVideoUnit")
        .mockResolvedValue({ unit: { ...unit, text: "推门而入。" }, unit_capability: makeReferenceUnitCapability("E1U1") });
      const genSpy = vi
        .spyOn(API, "generateReferenceVideoUnit")
        .mockResolvedValue({ task_id: "t1", deduped: false });
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

      fireEvent.change(await screen.findByRole("combobox"), { target: { value: "推门而入。" } });
      fireEvent.click(screen.getByRole("button", { name: /^(Save and generate|保存并生成)$/ }));

      await waitFor(() => expect(genSpy).toHaveBeenCalled());
      expect(patchSpy).toHaveBeenCalledWith("proj", 1, "E1U1", { prompt: "推门而入。" });
      expect(patchSpy.mock.invocationCallOrder[0]).toBeLessThan(genSpy.mock.invocationCallOrder[0]);
    });

    it("正文保存失败时不入队，错误留在提示条上", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1", "推门。")], unit_capabilities: {} });
      vi.spyOn(API, "patchReferenceVideoUnit").mockRejectedValue(new Error("磁盘已满"));
      const genSpy = vi.spyOn(API, "generateReferenceVideoUnit");
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

      fireEvent.change(await screen.findByRole("combobox"), { target: { value: "推门而入。" } });
      fireEvent.click(screen.getByRole("button", { name: /^(Save and generate|保存并生成)$/ }));

      expect(await screen.findByText(/磁盘已满/)).toBeInTheDocument();
      expect(genSpy).not.toHaveBeenCalled();
    });

    it("有未保存的正文时切换单元先询问，放弃修改后才切换", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1", "hello from A"), mkUnit("E1U2", "hello from B")], unit_capabilities: {} });
      const patchSpy = vi.spyOn(API, "patchReferenceVideoUnit");
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      fireEvent.change(await screen.findByRole("combobox"), { target: { value: "edited A" } });

      fireEvent.click(screen.getByTestId("unit-row-E1U2"));
      const dialog = within(await screen.findByRole("alertdialog"));
      expect(dialog.getByText(/「U1」有未保存的修改|“U1” has unsaved changes/)).toBeInTheDocument();
      expect(dialog.getByRole("button", { name: /^(Save and switch|保存并切换)$/ })).toBeInTheDocument();
      expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U1");

      fireEvent.click(dialog.getByRole("button", { name: /^(Discard changes|放弃修改)$/ }));
      await waitFor(() => expect((screen.getByRole("combobox") as HTMLTextAreaElement).value).toBe("hello from B"));
      expect(patchSpy).not.toHaveBeenCalled();
    });

    it.each([
      ["", "/episodes/1"],
      ["/app/projects/proj", "/episodes/1"],
    ])("同一集的画布视图之间切换不询问，去剪辑视图时询问（base=%s）", async (base, path) => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1", "推门。")], unit_capabilities: {} });
      renderAt(`${path}?view=board`, base);
      fireEvent.change(await screen.findByRole("combobox"), { target: { value: "推门而入。" } });

      act(() => navigate(`${path}?view=plan`));
      expect(window.location.pathname + window.location.search).toBe(`${base}${path}?view=plan`);
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

      act(() => navigate(`${path}?view=edit`));
      expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
      expect(window.location.search).toBe("?view=plan");
    });
  });

  // 未登记的 `@[名称]` 只是提示：保存与生成入口都不受影响。
  it("keeps saving and generating available when the body mentions an unregistered name", async () => {
    const unit = mkUnit("E1U1", "推门。");
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit], unit_capabilities: {} });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    const ta = await screen.findByRole("combobox");
    fireEvent.change(ta, { target: { value: "@[查无此人] 推门而入。" } });

    expect(await screen.findByRole("button", { name: /^(Save|保存)$/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /^(Save and generate|保存并生成)$/ })).toBeEnabled();
  });

  // 时长是 unit 级单一真相：下拉档位来自服务端逐单元结论，选中即单独 PATCH（不牵连正文草稿）
  it("renders the unit duration dropdown from the server's per-unit tiers and patches on change", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1")],
      unit_capabilities: { E1U1: makeReferenceUnitCapability("E1U1", { allowed_durations: [3, 8] }) },
    });
    const patchSpy = vi.spyOn(API, "patchReferenceVideoUnit").mockResolvedValue({
      unit: { ...mkUnit("E1U1"), duration_seconds: 8 },
      unit_capability: makeReferenceUnitCapability("E1U1"),
    });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    expect(await openDurationOptions()).toEqual([3, 8]);
    await userEvent.click(screen.getByRole("option", { name: /^8/ }));
    await waitFor(() =>
      expect(patchSpy).toHaveBeenCalledWith("proj", 1, "E1U1", { duration_seconds: 8 }),
    );
  });

  it("commits a free-form duration once after editing instead of patching intermediate digits", async () => {
    const unit = mkUnit("E1U1");
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit], unit_capabilities: {} });
    const patchSpy = vi
      .spyOn(API, "patchReferenceVideoUnit")
      .mockResolvedValue({ unit: { ...unit, duration_seconds: 120 }, unit_capability: makeReferenceUnitCapability("E1U1") });

    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} freeDuration />);
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
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit], unit_capabilities: {} });
    const patchSpy = vi.spyOn(API, "patchReferenceVideoUnit");

    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} freeDuration />);
    const input = await screen.findByRole("spinbutton", { name: /Duration|时长/ });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "12" } });

    // 离开拦截在关闭标签页时请求浏览器原生提示
    await waitFor(() => {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(true);
    });
    expect(patchSpy).not.toHaveBeenCalled();
  });

  it("lets an explicit same-value duration confirm a duration-only replan marker", async () => {
    const unit = { ...mkUnit("E1U1"), needs_replan: true };
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit], unit_capabilities: {} });
    const patchSpy = vi
      .spyOn(API, "patchReferenceVideoUnit")
      .mockResolvedValue({ unit: { ...unit, needs_replan: false }, unit_capability: makeReferenceUnitCapability("E1U1") });

    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} freeDuration />);
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
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1")],
      unit_capabilities: { E1U1: makeReferenceUnitCapability("E1U1") },
    });
    vi.spyOn(API, "patchReferenceVideoUnit").mockResolvedValue({
      unit: { ...mkUnit("E1U1"), duration_seconds: 8 },
      unit_capability: makeReferenceUnitCapability("E1U1"),
    });
    const fetchSpy = vi.spyOn(useCostStore.getState(), "debouncedFetch").mockImplementation(() => {});
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    await openDurationOptions();
    await userEvent.click(screen.getByRole("option", { name: /^8/ }));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledWith("proj"));
  });

  // 换模型后档位收窄，已保存的越界秒数仍要留在选项里，否则下拉会把它静默改写
  it("keeps an out-of-slot saved duration as an option", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1")],
      unit_capabilities: { E1U1: makeReferenceUnitCapability("E1U1", { allowed_durations: [4, 8] }) },
    });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    expect(await screen.findByRole("combobox", { name: /Duration|时长/ })).toHaveTextContent(/^3/);
    expect(await openDurationOptions()).toEqual([3, 4, 8]);
  });

  // 桶由服务端按此刻可用的参考图判定：正文提及了已登记的角色但角色还没有参考图，服务端落 i2v，
  // 画布照用 i2v 档位，不按「名字已登记」自行改判成 r2v。
  it("follows the server bucket for a registered-but-imageless mention instead of judging the body", async () => {
    useProjectsStore.setState({
      currentProjectName: "proj",
      currentProjectData: { ...STUB_PROJECT, characters: { 王: { description: "" } } },
    });
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1", "@[王] 推门。")],
      unit_capabilities: {
        E1U1: makeReferenceUnitCapability("E1U1", {
          declared_capability: "r2v",
          hydrated_capability: "i2v",
          declared_references: [{ type: "character", name: "王" }],
          unavailable_references: [{ type: "character", name: "王" }],
          allowed_durations: [4, 8],
          problems: [
            {
              code: "reference_asset_missing",
              blocking: true,
              unit_id: "E1U1",
              locations: [],
              params: { missing: [["character", "王"]] },
              action: "repair_reference_assets",
            },
          ],
        }),
      },
    });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    expect(await openDurationOptions()).toEqual([3, 4, 8]);
    // 分裂以结构化块点名：缺图的引用与桶的改变，不静默换桶。
    const alert = screen.getByTestId("reference-split-alert");
    expect(alert).toHaveTextContent("引用的资产缺图：王");
    expect(alert).toHaveTextContent("本单元将按图生视频（无参考图）执行，而非声明的参考生视频（带参考图）");
  });

  it("hides the split block when declared references all have images", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1", "@[王] 推门。")],
      unit_capabilities: {
        E1U1: makeReferenceUnitCapability("E1U1", {
          declared_capability: "r2v",
          hydrated_capability: "r2v",
          declared_references: [{ type: "character", name: "王" }],
          allowed_durations: [8],
        }),
      },
    });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    expect(await openDurationOptions()).toEqual([3, 8]);
    expect(screen.queryByTestId("reference-split-alert")).not.toBeInTheDocument();
  });

  // 服务端给不出该单元所落桶的档位（该字段为 null）：控件降级为只读，不回落到另一桶的档位。
  it("shows a read-only duration when the unit's tiers are unknown", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1")],
      unit_capabilities: { E1U1: makeReferenceUnitCapability("E1U1", { allowed_durations: null }) },
    });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    expect((await screen.findAllByText(/^3 ?(s|秒)/)).length).toBeGreaterThan(0);
    expect(screen.queryByRole("combobox", { name: /Duration|时长/ })).not.toBeInTheDocument();
  });

  it("shows the bucket's failure and repair guidance when its request facts cannot be resolved", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1")],
      unit_capabilities: {
        E1U1: makeReferenceUnitCapability("E1U1", {
          allowed_durations: null,
          problem: {
            code: "reference_capability_unavailable",
            params: { capability: "i2v" },
            action: "configure_video_model",
          },
        }),
      },
    });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    expect(await screen.findByText("图生视频（无参考图）档位未知")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("reference_capability_unavailable");
    expect(screen.queryByRole("combobox", { name: /Duration|时长/ })).not.toBeInTheDocument();
  });

  it.each([true, false])("uses the unit's endpoint-fixed flag from the server (%s)", async (fixed) => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1")],
      unit_capabilities: {
        E1U1: makeReferenceUnitCapability("E1U1", {
          allowed_durations: fixed ? null : [4, 8],
          duration_endpoint_fixed: fixed,
          duration_endpoint_fixed_reason: fixed ? "workflow_fixed" : null,
        }),
      },
    });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    await screen.findByTestId("unit-row-E1U1");
    expect(screen.queryByRole("combobox", { name: /Duration|时长/ }) === null).toBe(fixed);
    expect(Boolean(screen.queryByText(/workflow/i))).toBe(fixed);
  });

  it("remounts the card so textarea shows the new unit's prompt when selection changes", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1", "hello from A"), mkUnit("E1U2", "hello from B")], unit_capabilities: {} });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    const taA = (await screen.findByRole("combobox")) as HTMLTextAreaElement;
    expect(taA.value).toContain("hello from A");
    fireEvent.click(screen.getByTestId("unit-row-E1U2"));
    await waitFor(() => {
      expect((screen.getByRole("combobox") as HTMLTextAreaElement).value).toContain("hello from B");
    });
  });

  it("adds a new unit via the store when the button is clicked", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [], unit_capabilities: {} });
    const addSpy = vi.spyOn(API, "addReferenceVideoUnit").mockResolvedValue({ unit: mkUnit("E1U1"), unit_capability: makeReferenceUnitCapability("E1U1") });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Add video unit|新增视频单元/ })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /Add video unit|新增视频单元/ }));
    await waitFor(() => expect(addSpy).toHaveBeenCalled());
  });

  // 视图由集页路由给出：脚本规划视图下不渲染 UnitList，切回视频单元视图时单元列表仍在。
  it("renders the script plan preview instead of the unit list on the plan view", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    vi.spyOn(API, "getScriptReview").mockResolvedValue({
      episode: 1,
      content_mode: "narration",
      status: "pending_review",
      fingerprint: "fp",
      confirmed_at: null,
      quarantine: null,
      supported_durations: null,
      duration_tiers: null,
      episode_target_duration: null,
      script_overwrite: null,
      content: { units: [{ unit_id: "E1U1", text: "shot text", duration_seconds: 5, source_text: "" }] },
    });
    const { rerender } = render(<ReferenceVideoCanvas {...BOARD} view="plan" projectName="proj" episode={1} />);
    // UnitList 被隐藏，改由预览面板渲染 script_plan 结构化中间态（只读高亮文稿）
    await waitFor(() => expect(screen.getByText("shot text")).toBeInTheDocument());
    expect(screen.queryByTestId("unit-row-E1U1")).not.toBeInTheDocument();
    rerender(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
  });

  // 默认选中第一个 unit，避免出现 "有 units 但 editor 区域显示占位" 的不一致状态。
  it("resets a stale selectedUnitId (e.g. from a previous episode) to the first unit of current units", async () => {
    // 模拟切换 episode 后残留的旧 selectedUnitId
    useReferenceVideoStore.setState({ selectedUnitId: "E99U42" });
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1", "first"), mkUnit("E1U2", "second")], unit_capabilities: {} });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    await waitFor(() => {
      expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U1");
    });
    const ta = (await screen.findByRole("combobox")) as HTMLTextAreaElement;
    expect(ta.value).toContain("first");
  });

  // prompt_authoring 剧本未生成时（仅 segmented）units 端点无脚本可拆、会 404：不发起 units 请求，
  // 避免用户先看到一个报错的 Unit 面板；剧本生成后补上首次拉取。
  it("skips loadUnits until hasScript flips true", async () => {
    const listSpy = vi
      .spyOn(API, "listReferenceVideoUnits")
      .mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
    vi.spyOn(API, "getScriptReview").mockResolvedValue({
      episode: 1,
      content_mode: "narration",
      status: "pending_review",
      fingerprint: "fp",
      confirmed_at: null,
      quarantine: null,
      supported_durations: null,
      duration_tiers: null,
      episode_target_duration: null,
      script_overwrite: null,
      content: { units: [{ unit_id: "E1U1", text: "shot text", duration_seconds: 5, source_text: "" }] },
    });
    const { rerender } = render(
      <ReferenceVideoCanvas {...BOARD} view="plan" projectName="proj" episode={1} hasScript={false} />,
    );
    await waitFor(() => expect(screen.getByText("shot text")).toBeInTheDocument());
    expect(listSpy).not.toHaveBeenCalled();
    rerender(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} hasScript={true} />);
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
  });

  // optimistic：任务队列 3s 轮询间隙内按钮也要立刻反馈 busy，否则用户
  // 会误以为"点了没反应"继续点击造成重复入队。
  it("flips the generate button to busy optimistically before the task poll picks it up", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
    // 用 deferred promise 模拟 202 响应尚未回来的中间态
    let resolveGen: (v: { task_id: string; deduped: boolean }) => void = () => {};
    const genSpy = vi.spyOn(API, "generateReferenceVideoUnit").mockReturnValue(
      new Promise((resolve) => {
        resolveGen = resolve;
      }),
    );
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    const btn = await screen.findByRole("button", { name: UNIT_GENERATE_CTA });
    // 点击前 tasks store 为空，按钮启用
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    await waitFor(() => expect(genSpy).toHaveBeenCalled());
    // 入队成功（202 返回）后、任务轮询写回前：动作层打乐观标记，按钮立即
    // busy 并显示 "Generating…/生成中"；请求飞行中的双击由后端去重索引兜底
    resolveGen({ task_id: "t1", deduped: false });
    await waitFor(() => expect(screen.getByRole("button", { name: /^(Generating|生成中)/ })).toBeDisabled());
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toMatch(/Queued for generation|已加入生成队列/);
    });
  });

  // 重试路径：旧失败行始终在，statusMap 的乐观分支（!queueRow）不生效，禁用须
  // 直接取占用集，否则入队到任务行落库之间的窗口内按钮可重复点击。
  it("重试失败 unit 后在真实任务行落库前即禁用按钮", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
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
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

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
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit], unit_capabilities: {} });
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
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

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
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
    const genSpy = vi
      .spyOn(API, "generateReferenceVideoUnit")
      .mockResolvedValue({ task_id: "t1", deduped: false } as never);
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    // 冻结两个响应式 hook——模拟面板渲染期捕获的 busy/status 未能反映随后落库的任务行
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

    // 锚定行首，避免匹配到批量入口「补齐视频」——它是另一条提交路径（见下一个用例）
    const generate = await screen.findByRole("button", { name: UNIT_GENERATE_CTA });
    expect(generate).toBeEnabled();

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
      units: [mkUnit("E1U1"), mkUnit("E1U2"), { ...mkUnit("E1U3"), needs_replan: true }], unit_capabilities: {} });
    const batchSpy = vi
      .spyOn(API, "generateReferenceVideoBatch")
      .mockResolvedValue(mkAdmission());
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    const batch = await screen.findByRole("button", { name: /Fill in videos|补齐视频/ });
    await waitFor(() => expect(batch).toBeEnabled());

    // 渲染之后、点击之前，E1U1 已被别的入口占用
    act(() => {
      useTasksStore.setState({ tasks: [runningTask("E1U1")] as never });
    });

    fireEvent.click(batch);
    await waitFor(() => expect(batchSpy).toHaveBeenCalledTimes(1));
    expect(batchSpy).toHaveBeenCalledWith("proj", 1, {
      unit_ids: ["E1U1", "E1U2", "E1U3"],
    });
  });

  it("批量入口一次请求走服务端准入，admitted 时提示已入队", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    const batchSpy = vi
      .spyOn(API, "generateReferenceVideoBatch")
      .mockResolvedValue(mkAdmission({
        task_ids: ["t1", "t2"],
        task_ids_by_unit: { E1U1: "t1", E1U2: "t2" },
      }));
    const unitSpy = vi.spyOn(API, "generateReferenceVideoUnit");

    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    const batch = await screen.findByRole("button", { name: /Fill in videos|补齐视频/ });
    await waitFor(() => expect(batch).toBeEnabled());
    fireEvent.click(batch);

    await waitFor(() =>
      expect(batchSpy).toHaveBeenCalledWith("proj", 1, {
        unit_ids: ["E1U1", "E1U2"],
      }),
    );
    // 不再逐个串行入队
    expect(unitSpy).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toMatch(/已提交 2 个视频生成任务|Queued 2 video/);
    });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("批量入口只提交目标单元，不提供也不携带旁白交付方式", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
    const batchSpy = vi
      .spyOn(API, "generateReferenceVideoBatch")
      .mockResolvedValue(mkAdmission());

    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    const batch = await screen.findByRole("button", { name: /Fill in videos|补齐视频/ });
    expect(screen.queryByRole("button", { name: /Use current TTS|使用当前 TTS/ })).not.toBeInTheDocument();
    await waitFor(() => expect(batch).toBeEnabled());
    fireEvent.click(batch);

    await waitFor(() => expect(batchSpy).toHaveBeenCalled());
    expect(batchSpy).toHaveBeenCalledWith("proj", 1, { unit_ids: ["E1U1"] });
  });

  // 上传与生成回写同一个成片文件：文件选择对话框打开期间同一 unit 可能已被占用，
  // 按钮渲染期的禁用态挡不住这段窗口，提交时刻须再用 getState() 新鲜读复核一次。
  it("上传确认时刻复核占用态，命中即拒绝并提示", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
    const uploadSpy = vi.spyOn(API, "uploadReferenceUnitVideo");
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    const { container } = render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
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
      units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    // 上传挂起不 resolve，模拟「请求已发出、尚未落盘」的窗口
    vi.spyOn(API, "uploadReferenceUnitVideo").mockReturnValue(new Promise(() => {}) as never);
    const batchSpy = vi
      .spyOn(API, "generateReferenceVideoBatch")
      .mockResolvedValue(mkAdmission());
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    const { container } = render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    const batch = await screen.findByRole("button", { name: /Fill in videos|补齐视频/ });
    await waitFor(() => expect(batch).toBeEnabled());

    // 选中项默认是 E1U1，其预览面板的上传入口即针对该 unit
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    fireEvent.change(input!, { target: { files: [new File(["x"], "clip.mp4", { type: "video/mp4" })] } });

    fireEvent.click(batch);
    await waitFor(() => expect(batchSpy).toHaveBeenCalledTimes(1));
    expect(batchSpy).toHaveBeenCalledWith("proj", 1, {
      unit_ids: ["E1U2"],
    });
  });

  // 复核把目标清空时界面必须说一句：两条提交路径都以裸 return 结束的话，用户看到的是
  // 「点了没反应」，会以为任务已经提交。
  it("复核后没有可提交的目标时给出提示", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
    vi.spyOn(API, "uploadReferenceUnitVideo").mockReturnValue(new Promise(() => {}) as never);
    const batchSpy = vi.spyOn(API, "generateReferenceVideoBatch");
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    const { container } = render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    const batch = await screen.findByRole("button", { name: /Fill in videos|补齐视频/ });
    await waitFor(() => expect(batch).toBeEnabled());

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
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
      const genSpy = vi
        .spyOn(API, "generateReferenceVideoUnit")
        .mockResolvedValue({ task_id: "t1", deduped: false } as never);
      stubPrecheck({
        needs_confirmation: true,
        script_duration: 5,
        request_duration: 8,
        adjustment: "up",
      });

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
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

    it("取消确认则不入队", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
      const genSpy = vi
        .spyOn(API, "generateReferenceVideoUnit")
        .mockResolvedValue({ task_id: "t1", deduped: false } as never);
      stubPrecheck({
        needs_confirmation: true,
        script_duration: 5,
        request_duration: 8,
        adjustment: "up",
      });

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      fireEvent.click(await screen.findByRole("button", { name: UNIT_GENERATE_CTA }));
      await screen.findByRole("button", { name: CONFIRM_CTA });

      fireEvent.click(screen.getByRole("button", { name: /Cancel|取消/ }));

      await waitFor(() =>
        expect(screen.queryByRole("button", { name: CONFIRM_CTA })).not.toBeInTheDocument(),
      );
      expect(genSpy).not.toHaveBeenCalled();
    });

    it("总时长超过最大档位时说明成片短于剧本编排", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
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

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      fireEvent.click(await screen.findByRole("button", { name: UNIT_GENERATE_CTA }));

      await screen.findByRole("button", { name: CONFIRM_CTA });
      expect(screen.getByText(/短 8 秒|8s shorter/)).toBeInTheDocument();
      expect(screen.getByText(/放不下完整的请求时长基准|cannot hold the full request basis/)).toBeInTheDocument();
    });




    // 反向：单元入口的「重新生成」本就要覆盖已有成片，不能被同一条复核挡掉。
    it("单元入口对已有成片的单元仍可重新生成", async () => {
      const ready = mkUnit("E1U1");
      ready.generated_assets.video_clip = "videos/E1U1.mp4";
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [ready], unit_capabilities: {} });
      const genSpy = vi
        .spyOn(API, "generateReferenceVideoUnit")
        .mockResolvedValue({ task_id: "t1", deduped: false } as never);
      stubPrecheck({
        needs_confirmation: true,
        script_duration: 5,
        request_duration: 8,
        adjustment: "up",
      });

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      fireEvent.click(await screen.findByRole("button", { name: /Regenerate|重新生成/ }));

      fireEvent.click(await screen.findByRole("button", { name: CONFIRM_CTA }));
      await waitFor(() =>
        expect(genSpy).toHaveBeenCalledWith("proj", 1, "E1U1", {
          confirmed_request_duration_seconds: 8,
        }),
      );
    });



    it("预检失败的单元不入队并提示", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
      const genSpy = vi
        .spyOn(API, "generateReferenceVideoUnit")
        .mockResolvedValue({ task_id: "t1", deduped: false } as never);
      vi.spyOn(API, "precheckReferenceVideoDuration").mockRejectedValue(new Error("offline"));

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      fireEvent.click(await screen.findByRole("button", { name: UNIT_GENERATE_CTA }));

      await waitFor(() => {
        expect(useAppStore.getState().toast?.text).toMatch(/时长核对失败|Could not check the length/);
      });
      expect(genSpy).not.toHaveBeenCalled();
    });
  });

  // 批量的三种结局都是评估成功：admitted 已建任务，confirmation_required 与 blocked
  // 一个任务也没建，界面必须把「为什么没开始」讲全，而不是塌成一句通用错误。admitted
  // 自身还分两路，入队中断那一路同样要讲全「哪几个没排上、为什么」。
  describe("整批准入判定", () => {
    const BATCH_CONFIRM_CTA = /Generate at these lengths|按这些档位生成/;

    async function clickBatch() {
      const batch = await screen.findByRole("button", { name: /Fill in videos|补齐视频/ });
      await waitFor(() => expect(batch).toBeEnabled());
      fireEvent.click(batch);
      return batch;
    }

    it("需确认时按档位聚合陈述，确认后带 confirmed_request_durations 重发", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2"), mkUnit("E1U3")], unit_capabilities: {} });
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

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      await clickBatch();

      const confirm = await screen.findByRole("button", { name: BATCH_CONFIRM_CTA });
      const dialog = within(screen.getByRole("alertdialog"));
      // 档位分组：秒数 × 单元数 + 合计费用；报价不全的档位不展示假合计
      expect(dialog.getByText(/^(?:8 秒|8s)$/)).toBeInTheDocument();
      expect(dialog.getByText(/2 个单元|2 units/)).toBeInTheDocument();
      expect(dialog.getByText(/约 \$1\.60|about \$1\.60/)).toBeInTheDocument();
      expect(dialog.getByText(/报价不可用|price unavailable/)).toBeInTheDocument();
      for (const unitId of ["U1", "U2", "U3"]) {
        expect(dialog.getByText(unitId)).toBeInTheDocument();
      }

      fireEvent.click(confirm);
      await waitFor(() => expect(batchSpy).toHaveBeenCalledTimes(2));
      expect(batchSpy).toHaveBeenLastCalledWith("proj", 1, {
        unit_ids: ["E1U1", "E1U2", "E1U3"],
        confirmed_request_durations: { E1U1: 8, E1U2: 8, E1U3: 4 },
      });
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    });

    // 弹窗停留时长由用户决定，可以很长：其间别处完成的单元若按冻结清单原样重发，队列
    // 去重（只看 queued/running）拦不住，会重跑一次生成、重复计费并覆盖成片。
    it("确认停留期间已完成的单元不再重发", async () => {
      const [u1, u2] = [mkUnit("E1U1"), mkUnit("E1U2")];
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [u1, u2], unit_capabilities: {} });
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

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
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
        confirmed_request_durations: { E1U2: 4 },
      });
    });

    it("受阻时列出每个单元的缺口与下一步，并标明被连带扣下的单元", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2"), mkUnit("E1U3")], unit_capabilities: {} });
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

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      await clickBatch();

      const dialog = within(await screen.findByRole("alertdialog"));
      // 全部缺口都在，不塌成第一条
      expect(dialog.getByText("引用的角色图缺失")).toBeInTheDocument();
      expect(dialog.getByText("该单元需要重新规划")).toBeInTheDocument();
      expect(dialog.getByText(/补上或更换缺失的参考素材|add or replace the missing reference/)).toBeInTheDocument();
      expect(dialog.getByText(/改写这个单元|rewrite this unit/)).toBeInTheDocument();
      // 申请档位与缺口同列，用户才看得出差多少
      expect(dialog.getByText(/^(?:申请 12 秒|requesting 12s)$/)).toBeInTheDocument();
      // 被连带扣下的单元单列，说明它自身没问题
      expect(dialog.getByText(/本身没问题|w(as|ere) fine but w(as|ere) held back/)).toBeInTheDocument();
      for (const unitId of ["U1", "U2", "U3"]) {
        expect(dialog.getByText(unitId)).toBeInTheDocument();
      }

      // 受阻是终局：关闭不重发
      fireEvent.click(dialog.getByRole("button", { name: /Got it|知道了/ }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(batchSpy).toHaveBeenCalledTimes(1);
    });

    it("入队中断时逐个列出没排上队列的单元与原因", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2"), mkUnit("E1U3")], unit_capabilities: {} });
      const batchSpy = vi.spyOn(API, "generateReferenceVideoBatch").mockResolvedValue(
        // 入队中断不撤销已建的任务：decision 仍是 admitted，没轮到的单元逐个进
        // enqueue_failures，各带一条已本地化的原因。
        mkAdmission({
          task_ids: ["t1"],
          task_ids_by_unit: { E1U1: "t1" },
          skipped_unit_ids: ["E1U4"],
          enqueue_failures: [
            {
              unit_id: "E1U2",
              problem: { code: "queue_unavailable", action: "retry", message: "队列暂时不可用", params: {} },
            },
            {
              unit_id: "E1U3",
              problem: { code: "generation_task_create_failed", action: "retry", message: "任务创建失败", params: {} },
            },
          ],
        }),
      );

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      await clickBatch();

      const dialog = within(await screen.findByRole("alertdialog"));
      // 逐个 unit 与各自原因都在，不塌成一句计数
      expect(dialog.getByText("U2")).toBeInTheDocument();
      expect(dialog.getByText("U3")).toBeInTheDocument();
      expect(dialog.getByText("队列暂时不可用")).toBeInTheDocument();
      expect(dialog.getByText("任务创建失败")).toBeInTheDocument();
      // 已排上的单元不混进未排上的清单
      expect(dialog.queryByText("U1")).not.toBeInTheDocument();
      // 已建任务照常执行这一点要说明，否则用户读不出这批是「部分成功」
      expect(
        dialog.getByText(/再次批量生成就会补上|generate the batch again/),
      ).toBeInTheDocument();
      // 已有产物而跳过的单元与没排上的不是一回事，这一路同样要交代
      expect(
        dialog.getByText(/已有视频，本次跳过|already had a video and is skipped/),
      ).toBeInTheDocument();
      // 动作层那条计数提示仍在，两处说的是同一件事
      await waitFor(() => {
        expect(useAppStore.getState().toast?.text).toMatch(/入队中断|Enqueue was interrupted/);
      });

      // 陈述型结局：关闭不重发
      fireEvent.click(dialog.getByRole("button", { name: /Got it|知道了/ }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(batchSpy).toHaveBeenCalledTimes(1);
    });

    it("首个目标就中断时不说成部分排上", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
      vi.spyOn(API, "generateReferenceVideoBatch").mockResolvedValue(
        // 中断落在第一个目标上：decision 仍是 admitted，但一个任务也没建成。
        mkAdmission({
          task_ids: [],
          task_ids_by_unit: {},
          enqueue_failures: [
            {
              unit_id: "E1U1",
              problem: { code: "queue_unavailable", action: "retry", message: "队列暂时不可用", params: {} },
            },
            {
              unit_id: "E1U2",
              problem: {
                code: "generation_enqueue_interrupted",
                action: "retry",
                message: "入队已中断，这个目标没有排上",
                params: {},
              },
            },
          ],
        }),
      );

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      await clickBatch();

      const dialog = within(await screen.findByRole("alertdialog"));
      // 缺口明细照旧逐个列出
      expect(dialog.getByText("U1")).toBeInTheDocument();
      expect(dialog.getByText("U2")).toBeInTheDocument();
      // 一个任务都没建时不能说「部分」，也不能说已提交的任务照常执行
      expect(
        dialog.getByText(/没有任务排上队列|No task from this batch was queued/),
      ).toBeInTheDocument();
      expect(dialog.queryByText(/部分单元未排上队列|Some units were not queued/)).not.toBeInTheDocument();
      expect(dialog.getByText(/没有任务在执行|nothing from this batch is running/)).toBeInTheDocument();
    });

    it("批量请求失败时提示错误且不留下结论面板", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
      vi.spyOn(API, "generateReferenceVideoBatch").mockRejectedValue(new Error("offline"));

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      await clickBatch();

      await waitFor(() => {
        expect(useAppStore.getState().toast?.text).toMatch(/批量生成请求失败|Batch generation request failed/);
      });
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
  });

  it("没有待生成 unit 时批量生成按钮禁用", async () => {
    // 唯一的 unit 已有成片：statusMap 为 ready，批量入口无作用对象
    const ready = mkUnit("E1U1");
    ready.generated_assets.video_clip = "videos/E1U1.mp4";
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [ready], unit_capabilities: {} });

    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

    const batch = await screen.findByRole("button", { name: /Fill in videos|补齐视频/ });
    await waitFor(() => expect(batch).toBeDisabled());
  });

  // 后台任务失败通知已统一迁移到全局 useTaskFailureNotifications hook（转变驱动 /
  // 历史失败不重报 / 同一失败只报一次回归均在那里覆盖），见
  // hooks/useTaskFailureNotifications.test.tsx。此处只验证回跳消费。

  // 通知回跳：收到 reference_unit scroll target 时切到视频单元视图并选中对应 unit，不多留一条历史。
  it("selects the unit and asks for the units view on a reference_unit scroll target", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    vi.spyOn(API, "getScriptReview").mockRejectedValue(new Error("not needed"));
    const onViewChange = vi.fn();
    render(<ReferenceVideoCanvas view="plan" onViewChange={onViewChange} projectName="proj" episode={1} />);
    await waitFor(() => expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U1"));

    useAppStore.getState().triggerScrollTo({ type: "reference_unit", id: "E1U2", route: "/episodes/1" });

    await waitFor(() => expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U2"));
    expect(useAppStore.getState().scrollTarget).toBeNull();
    expect(onViewChange).toHaveBeenCalledWith("board", { replace: true });
  });

  it("窄屏下带起始时间的单元链接把预览子页签切到前台", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    await waitFor(() => expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U1"));
    const previewTab = screen.getByRole("tab", { name: /^(Video|视频)$/ });
    expect(previewTab).toHaveAttribute("aria-selected", "false");

    act(() => {
      useAppStore.getState().triggerScrollTo({ type: "reference_unit", id: "E1U2", route: "/episodes/1" });
      useAppStore.getState().requestPlaybackStart({ resource_type: "reference_videos", resource_id: "E1U2", seconds: 2 });
    });

    await waitFor(() => expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U2"));
    expect(screen.getByRole("tab", { name: /^(Video|视频)$/ })).toHaveAttribute("aria-selected", "true");
    act(() => useAppStore.getState().clearPlaybackStart());
  });

  // 慢网/冷启动回归：units 仍在加载（loadUnits 未返回）时，即便 target 已过期也不该
  // 提前清除——否则 units 到达后无法再选中目标 unit，"点击通知回跳"失效。
  it("keeps a reference_unit target while units are still loading, even past expiry", async () => {
    let resolveList: (v: { units: ReferenceVideoUnit[]; unit_capabilities: ReferenceUnitCapabilityMap }) => void = () => {};
    vi.spyOn(API, "listReferenceVideoUnits").mockReturnValue(
      new Promise((resolve) => {
        resolveList = resolve;
      }),
    );
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
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
      resolveList({ units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    });
    await waitFor(() => expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U2"));
    expect(useAppStore.getState().scrollTarget).toBeNull();
  });

  // 兜底回归：units 加载完成但目标 unit 不存在时，即便此后没有任何依赖变化，
  // 过期 target 也应被一次性定时器清除，不会永久残留 store。
  it("clears an unresolvable reference_unit target after expiry without further updates", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
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
      .mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
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
          // 横幅只在参考音频绑定档下出现：提示词软约束档不挂参考音频，换了也不改变已生成视频的声音
          character_voice_binding: "reference_audio",
          characters: { 王: { description: "", voice_updated_at: VOICE_UPDATED_AT } },
        },
        refreshProject: vi.fn().mockResolvedValue({ status: "ok" }),
      } as never);
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [staleUnit()], unit_capabilities: {} });
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
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
          character_voice_binding: "reference_audio",
          characters: { 王: { description: "", voice_updated_at: VOICE_UPDATED_AT } },
        },
        refreshProject,
      } as never);
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [staleUnit()], unit_capabilities: {} });
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

      const dismiss = await screen.findByRole("button", { name: /Got it|知道了/ });
      fireEvent.click(dismiss);

      await waitFor(() => expect(useAppStore.getState().toast?.tone).toBe("error"));
    });

    it("提示词软约束档下不出现——该档不挂参考音频，横幅会指向一个当前不生效的设置", async () => {
      useProjectsStore.setState({
        currentProjectName: "proj",
        currentProjectData: {
          ...STUB_PROJECT,
          character_voice_binding: "prompt",
          characters: { 王: { description: "", voice_updated_at: VOICE_UPDATED_AT } },
        },
        refreshProject: vi.fn().mockResolvedValue({ status: "ok" }),
      } as never);
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [staleUnit()], unit_capabilities: {} });
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

      await screen.findAllByText("U1");
      expect(screen.queryByRole("button", { name: /Got it|知道了/ })).not.toBeInTheDocument();
    });
  });
});
