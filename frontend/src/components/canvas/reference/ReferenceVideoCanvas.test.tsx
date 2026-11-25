import { seedTaskState } from "@/test/task-state";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render as rtlRender, screen, waitFor, fireEvent, act, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { Router, useLocation } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { LeaveGuardProvider, useConfirmLeave, useLeaveGuard } from "@/components/shared/edit-unit/LeaveGuard";
import { EpisodePage } from "@/components/canvas/episode-page/EpisodePage";
import { EpisodeViewFactsProvider } from "@/components/canvas/episode-page/EpisodeViewScope";
import type { EpisodeViewFacts } from "@/components/canvas/episode-page/episode-view";
import { ReferenceVideoCanvas } from "./ReferenceVideoCanvas";
import { useReferenceVideoStore, referenceVideoCacheKey } from "@/stores/reference-video-store";
import { useProjectsStore } from "@/stores/projects-store";
import {
  defaultTaskStats,
  useActiveResourceIds,
  useLatestTasksByResource,
  useTasksStore,
} from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useCostStore } from "@/stores/cost-store";
import { API, ApiRequestError, ReferenceProjectionError } from "@/api";
import { makeReferenceUnitCapability, makeTask } from "@/test/factories";
import addUnitContract from "@/test/contracts/reference-unit-add-requests.json";
import { createDeferred } from "@/test/deferred";
import type {
  PromptOverwrite,
  ReferenceDurationPrecheck,
  ReferenceUnitCapabilityMap,
  ReferenceVideoUnit,
  UnitGeneratedAssets,
} from "@/types";
import type { EpisodeScript, ProjectData } from "@/types";

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

function mkPreview(patch: Record<string, unknown> = {}) {
  return {
    decision: "admitted",
    operation: "generate_reference_videos_batch",
    selection: "explicit",
    units: [],
    confirmation: null,
    skipped_unit_ids: [],
    estimated_cost: null,
    unpriced_count: 0,
    ...patch,
  } as never;
}

const BATCH_START_CTA = /^(Start generating|开始生成)$/;

async function confirmBatchPreview(name: RegExp = BATCH_START_CTA) {
  const confirm = await screen.findByRole("button", { name });
  await waitFor(() => expect(confirm).toBeEnabled());
  fireEvent.click(confirm);
}

const UNIT_GENERATE_CTA = /^(Generate video|生成视频)/;

function render(ui: ReactElement) {
  return rtlRender(ui, { wrapper: LeaveGuardProvider });
}

async function openDurationOptions(): Promise<number[]> {
  await userEvent.click(await screen.findByRole("combobox", { name: /Duration|时长/ }));
  return (await screen.findAllByRole("option")).map((option) => Number.parseInt(option.textContent ?? "", 10));
}

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

function seedFormalUnits(unitIds: string[]) {
  const script = { episode: 1, video_units: unitIds.map((unit_id) => ({ unit_id, text: "x" })) };
  useProjectsStore.setState({ currentScripts: { "episode_1.json": script as unknown as EpisodeScript } });
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
    useProjectsStore.setState({ currentProjectName: "proj", currentProjectData: STUB_PROJECT, currentScripts: {} });
    seedTaskState({
      tasks: [],
      connected: false,
      optimisticMarks: new Map(),
    });
    useAppStore.setState({ toast: null });
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
    vi.spyOn(API, "previewReferenceVideoBatch").mockImplementation(async (_project, _episode, payload) =>
      mkPreview({
        units: (payload.unit_ids ?? []).map((unitId) => ({ unit_id: unitId, admitted: true, problems: [] })),
      }),
    );
  });
  afterEach(() => vi.restoreAllMocks());

  it("loads units on mount and renders the list", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
    expect(screen.getByTestId("unit-row-E1U2")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "视频单元 1 / 2" })).toHaveTextContent("1 / 2");
  });

  it("renders units that carry no generated_assets section at all", async () => {
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
    expect(within(dialog).getByText("移除视频单元 1？")).toBeInTheDocument();
    expect(deleteSpy).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: /^(Move to project trash|移入项目回收站)$/ }));

    await waitFor(() => expect(deleteSpy).toHaveBeenCalledWith("proj", 1, "E1U1"));
    await waitFor(() => expect(screen.queryByTestId("unit-row-E1U1")).not.toBeInTheDocument());
    expect(screen.getByTestId("unit-row-E1U2")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("asks about unsaved edits only once the removal is confirmed, so cancelling the removal keeps them", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    const deleteSpy = vi.spyOn(API, "deleteReferenceVideoUnit").mockResolvedValue(undefined);
    const discard = vi.fn();
    function DirtyUnit() {
      useLeaveGuard({ dirty: true, save: async () => true, discard });
      return null;
    }
    render(<><DirtyUnit /><ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} /></>);
    await screen.findByTestId("unit-row-E1U1");
    const removal = async () => {
      fireEvent.click(screen.getByRole("button", { name: /^(Remove unit|移除单元)$/ }));
      const dialog = await screen.findByRole("alertdialog");
      expect(within(dialog).getByText("移除视频单元 1？")).toBeInTheDocument();
      return dialog;
    };

    fireEvent.click(within(await removal()).getByRole("button", { name: /^(Cancel|取消)$/ }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(discard).not.toHaveBeenCalled();

    fireEvent.click(within(await removal()).getByRole("button", { name: /^(Move to project trash|移入项目回收站)$/ }));
    fireEvent.click(within(await screen.findByRole("alertdialog", { name: "有未保存的修改" })).getByRole("button", { name: "放弃修改" }));

    await waitFor(() => expect(deleteSpy).toHaveBeenCalledWith("proj", 1, "E1U1"));
    expect(discard).toHaveBeenCalledTimes(1);
  });

  it("does not remove a unit that became busy after the confirmation opened", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    const deleteSpy = vi.spyOn(API, "deleteReferenceVideoUnit").mockResolvedValue(undefined);
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    fireEvent.click(await screen.findByRole("button", { name: /^(Remove unit|移除单元)$/ }));

    act(() => {
      seedTaskState({ tasks: [runningTask("E1U1")] as never });
    });
    const confirm = within(screen.getByRole("alertdialog")).getByRole("button", { name: /^(Move to project trash|移入项目回收站)$/ });
    await waitFor(() => expect(confirm).toBeDisabled());
    fireEvent.click(confirm);

    expect(deleteSpy).not.toHaveBeenCalled();
  });

  describe("确认移除并放弃正文修改后", () => {
    const UNITS = [mkUnit("E1U1", "server text"), mkUnit("E1U2", "next unit")];
    const removeButton = /^(Remove unit|移除单元)$/;
    const confirmButton = /^(Move to project trash|移入项目回收站)$/;

    async function editThenConfirmRemoval() {
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      fireEvent.change(await screen.findByRole("combobox"), { target: { value: "unsaved edit" } });
      fireEvent.click(screen.getByRole("button", { name: removeButton }));
      fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: confirmButton }));
      return screen.findByRole("alertdialog", { name: /有未保存的修改/ });
    }

    it("移除请求失败时正文修改原样保留", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: UNITS, unit_capabilities: {} });
      const deleteSpy = vi.spyOn(API, "deleteReferenceVideoUnit").mockRejectedValue(new Error("网络中断"));

      fireEvent.click(within(await editThenConfirmRemoval()).getByRole("button", { name: "放弃修改" }));

      await waitFor(() => expect(useAppStore.getState().toast?.text).toContain("网络中断"));
      expect(deleteSpy).toHaveBeenCalledTimes(1);
      expect(screen.getByDisplayValue("unsaved edit")).toBeInTheDocument();
    });

    it("询问未保存修改期间单元变为占用：提示占用，不移除，修改保留", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: UNITS, unit_capabilities: {} });
      const deleteSpy = vi.spyOn(API, "deleteReferenceVideoUnit").mockResolvedValue(undefined);
      const leaveDialog = await editThenConfirmRemoval();

      act(() => {
        seedTaskState({ tasks: [runningTask("E1U1")] as never });
      });
      fireEvent.click(within(leaveDialog).getByRole("button", { name: "放弃修改" }));

      await waitFor(() => expect(useAppStore.getState().toast?.text).toBe("该视频单元正在生成，请稍后再试"));
      expect(deleteSpy).not.toHaveBeenCalled();
      expect(screen.getByDisplayValue("unsaved edit")).toBeInTheDocument();
    });

    it("移除成功后不把被移除的单元当作外部删除保留", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: UNITS, unit_capabilities: {} });
      vi.spyOn(API, "deleteReferenceVideoUnit").mockResolvedValue(undefined);

      fireEvent.click(within(await editThenConfirmRemoval()).getByRole("button", { name: "放弃修改" }));

      await waitFor(() => expect(screen.queryByTestId("unit-row-E1U1")).not.toBeInTheDocument());
      expect(screen.queryByText(/这个视频单元已被删除/)).not.toBeInTheDocument();
      expect(screen.queryByDisplayValue("unsaved edit")).not.toBeInTheDocument();
    });
  });

  it("blocks removing a unit while its narration audio is being generated", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    seedTaskState({ tasks: [{ ...runningTask("E1U1"), task_type: "tts" }] as never });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

    await screen.findByTestId("unit-row-E1U1");
    expect(screen.getByRole("button", { name: /^(Remove unit|移除单元)$/ })).toBeDisabled();
  });

  it("外部删除当前视频单元时保留可见修改，放弃后采用真实状态", async () => {
    const units = [mkUnit("E1U1", "server text"), mkUnit("E1U2", "next unit")];
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units, unit_capabilities: {} });
    vi.spyOn(API, "patchReferenceVideoUnit").mockRejectedValue(new Error("单元已不存在"));
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    fireEvent.change(await screen.findByRole("combobox"), { target: { value: "unsaved edit" } });
    act(() => useReferenceVideoStore.setState((state) => ({ unitsByEpisode: { ...state.unitsByEpisode, [referenceVideoCacheKey("proj", 1)]: [units[1]] } })));
    expect(screen.getByRole("combobox")).toHaveValue("unsaved edit");
    expect(screen.getByText(/这个视频单元已被删除/)).toHaveAttribute("role", "status");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("单元已不存在");
    expect(screen.getByRole("combobox")).toHaveValue("unsaved edit");
    fireEvent.click(screen.getByRole("button", { name: "放弃修改" }));
    expect(screen.getByRole("combobox")).toHaveValue("next unit");
  });

  it("auto-selects first unit on load and shows preview generate button", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generate video|生成视频/ })).toBeInTheDocument();
    });
  });

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

  it("resolves an asset named __proto__ in the highlight lookup", async () => {
    useProjectsStore.setState({
      currentProjectName: "proj",
      currentProjectData: {
        ...STUB_PROJECT,
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

    const panel = await screen.findByRole("tabpanel");
    const mention = (await within(panel).findAllByText(/__proto__/)).find((el) =>
      el.className.includes("asset-character"),
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
    sheet.getByRole("button", { name: "调整「视频单元 2」的顺序", description: /空格|space/i }).focus();
    await userEvent.keyboard(" ");
    await waitFor(() => expect(screen.getByText(/已拿起「视频单元 2」/)).toBeInTheDocument());
    await userEvent.keyboard("{ArrowUp}");
    await waitFor(() => expect(screen.getByText(/「视频单元 2」移到第 1 项/)).toBeInTheDocument());
    await userEvent.keyboard(" ");

    await waitFor(() => expect(moveSpy).toHaveBeenCalledWith("proj", 1, "E1U2", null));
  });

  describe("正文编辑单元", () => {
    let navigate: (to: string) => void = () => {};
    function Navigator() {
      [, navigate] = useLocation();
      return null;
    }
    const FACTS: EpisodeViewFacts = {
      isAd: false,
      route: "reference_video",
      grid: false,
      hasScript: true,
      hasDraft: true,
      sourceReview: false,
      demo: false,
    };
    function renderAt(path: string, base = "") {
      window.history.replaceState(null, "", `${base}${path}`);
      return rtlRender(
        <LeaveGuardProvider>
          <Router base={base}>
            <Navigator />
            <EpisodeViewFactsProvider value={FACTS}>
              <ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />
            </EpisodeViewFactsProvider>
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

    it("旁白同样先保存正文再生成", async () => {
      const unit = mkUnit("E1U1", "镜头推进。\n{夜色深沉。}");
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit], unit_capabilities: {} });
      useProjectsStore.setState({ currentProjectData: { ...STUB_PROJECT, narration_delivery: "use_tts", episodes: [{ episode: 1, title: "", script_file: "episode_1.json" }] } });
      const save = vi.spyOn(API, "patchReferenceVideoUnit").mockResolvedValue({ unit: { ...unit, text: "镜头推进。\n{天亮了。}" }, unit_capability: makeReferenceUnitCapability("E1U1", { allowed_durations: null }) });
      const generate = vi.spyOn(API, "generateNarrationAudio").mockResolvedValue({ success: true, task_id: "tts-1", deduped: false, message: "queued" });
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      fireEvent.change(await screen.findByRole("combobox"), { target: { value: "镜头推进。\n{天亮了。}" } });
      fireEvent.click(screen.getAllByRole("button", { name: "保存并生成" }).at(-1)!);
      await waitFor(() => expect(generate).toHaveBeenCalled());
      expect(save.mock.invocationCallOrder[0]).toBeLessThan(generate.mock.invocationCallOrder[0]);
      expect(save).toHaveBeenCalledWith("proj", 1, "E1U1", { prompt: "镜头推进。\n{天亮了。}" });
    });

    it("有修改时预览先保存正文", async () => {
      const unit = mkUnit("E1U1", "推门。");
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit], unit_capabilities: {} });
      const patch = vi.spyOn(API, "patchReferenceVideoUnit").mockResolvedValue({ unit: { ...unit, text: "推门而入。" }, unit_capability: makeReferenceUnitCapability("E1U1") });
      const preview = vi.spyOn(API, "previewReferenceUnitPrompt").mockResolvedValue({ text: "最终提示词", is_text_form: true, unavailable: null, references: [], warnings: [] });
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      fireEvent.change(await screen.findByRole("combobox"), { target: { value: "推门而入。" } });
      fireEvent.click(screen.getByRole("button", { name: "保存并预览" }));
      expect(await screen.findByText("最终提示词")).toBeInTheDocument();
      expect(patch.mock.invocationCallOrder[0]).toBeLessThan(preview.mock.invocationCallOrder[0]);
      expect(patch).toHaveBeenCalledWith("proj", 1, "E1U1", { prompt: "推门而入。" });
    });

    it("正文保存失败时不请求预览", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1", "推门。")], unit_capabilities: {} });
      vi.spyOn(API, "patchReferenceVideoUnit").mockRejectedValue(new Error("正文写入失败"));
      const preview = vi.spyOn(API, "previewReferenceUnitPrompt");
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      fireEvent.change(await screen.findByRole("combobox"), { target: { value: "推门而入。" } });
      fireEvent.click(screen.getByRole("button", { name: "保存并预览" }));
      expect(await screen.findByText(/正文写入失败/)).toBeInTheDocument();
      expect(preview).not.toHaveBeenCalled();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(screen.getByRole("combobox")).toHaveValue("推门而入。");
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
      expect(dialog.getByText("「视频单元 1」有未保存的修改")).toBeInTheDocument();
      expect(dialog.getByRole("button", { name: /^(Save and switch|保存并切换)$/ })).toBeInTheDocument();
      expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U1");

      fireEvent.click(dialog.getByRole("button", { name: /^(Discard changes|放弃修改)$/ }));
      await waitFor(() => expect((screen.getByRole("combobox") as HTMLTextAreaElement).value).toBe("hello from B"));
      expect(patchSpy).not.toHaveBeenCalled();
    });

    it.each([
      ["", "/episodes/1"],
      ["/app/projects/proj", "/episodes/1"],
    ])("有未保存的正文时切到脚本规划会询问（base=%s）", async (base, path) => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1", "推门。")], unit_capabilities: {} });
      renderAt(`${path}?view=board`, base);
      fireEvent.change(await screen.findByRole("combobox"), { target: { value: "推门而入。" } });

      act(() => navigate(`${path}?view=plan`));
      expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
      expect(window.location.search).toBe("?view=board");
    });

    it.each([
      ["", "/episodes/1"],
      ["/app/projects/proj", "/episodes/1"],
    ])("停留在视频单元视图的跳转不询问，去剪辑视图时询问（base=%s）", async (base, path) => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1", "推门。")], unit_capabilities: {} });
      renderAt(`${path}?view=board`, base);
      fireEvent.change(await screen.findByRole("combobox"), { target: { value: "推门而入。" } });

      act(() => navigate(path));
      expect(window.location.pathname + window.location.search).toBe(`${base}${path}`);
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

      act(() => navigate(`${path}?view=edit`));
      expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
      expect(window.location.search).toBe("");
    });
  });

  it("keeps saving and generating available when the body mentions an unregistered name", async () => {
    const unit = mkUnit("E1U1", "推门。");
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit], unit_capabilities: {} });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    const ta = await screen.findByRole("combobox");
    fireEvent.change(ta, { target: { value: "@[查无此人] 推门而入。" } });

    expect(await screen.findByRole("button", { name: /^(Save|保存)$/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /^(Save and generate|保存并生成)$/ })).toBeEnabled();
  });

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

    await waitFor(() => {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(true);
    });
    expect(patchSpy).not.toHaveBeenCalled();
  });

  it("时长失焦提交在途时切换单元，等提交落定后直接切换，不询问也不重复提交", async () => {
    const units = [mkUnit("E1U1"), mkUnit("E1U2")];
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units, unit_capabilities: {} });
    let accept: () => void = () => {};
    const patchSpy = vi.spyOn(API, "patchReferenceVideoUnit").mockImplementation(
      () =>
        new Promise((resolve) => {
          accept = () => resolve({ unit: { ...units[0], duration_seconds: 12 }, unit_capability: makeReferenceUnitCapability("E1U1") });
        }),
    );

    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} freeDuration />);
    const input = await screen.findByRole("spinbutton", { name: /Duration|时长/ });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "12" } });
    fireEvent.blur(input);
    fireEvent.click(screen.getByTestId("unit-row-E1U2"));

    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U1");
    await act(async () => accept());
    await waitFor(() => expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U2"));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(patchSpy).toHaveBeenCalledTimes(1);
  });

  it("外部删除当前视频单元时保留尚未提交的自由时长，并说明单元已被删除", async () => {
    const units = [mkUnit("E1U1"), { ...mkUnit("E1U2"), duration_seconds: 8 }];
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units, unit_capabilities: {} });

    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} freeDuration />);
    const input = await screen.findByRole("spinbutton", { name: /Duration|时长/ });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "12" } });
    act(() => useReferenceVideoStore.setState((state) => ({ unitsByEpisode: { ...state.unitsByEpisode, [referenceVideoCacheKey("proj", 1)]: [units[1]] } })));

    expect(screen.getByRole("spinbutton", { name: /Duration|时长/ })).toHaveValue(12);
    expect(screen.getByText(/这个视频单元已被删除/)).toHaveAttribute("role", "status");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
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

  it("keeps an out-of-slot saved duration as an option", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1")],
      unit_capabilities: { E1U1: makeReferenceUnitCapability("E1U1", { allowed_durations: [4, 8] }) },
    });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    expect(await screen.findByRole("combobox", { name: /Duration|时长/ })).toHaveTextContent(/^3/);
    expect(await openDurationOptions()).toEqual([3, 4, 8]);
  });

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
    expect(screen.getByRole("alert")).not.toHaveTextContent("reference_capability_unavailable");
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

  describe("新增视频单元", () => {
    const addDialog = async () => {
      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByText("新增视频单元")).toBeInTheDocument();
      return dialog;
    };
    const confirmAdd = (dialog: HTMLElement) => within(dialog).getByRole("button", { name: "新增单元" });

    it("从空白开始的集填写正文后建出第一个单元，正文为空时不发请求", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [], unit_capabilities: {} });
      const created = mkUnit("E1U1", addUnitContract.append.prompt);
      const addSpy = vi
        .spyOn(API, "addReferenceVideoUnit")
        .mockResolvedValue({ unit: created, unit_capability: makeReferenceUnitCapability("E1U1") });
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} hasScript />);

      fireEvent.click(await screen.findByRole("button", { name: "新增第一个视频单元" }));
      const dialog = await addDialog();
      const prompt = within(dialog).getByRole("textbox", { name: "正文" });
      expect(confirmAdd(dialog)).toBeDisabled();
      fireEvent.change(prompt, { target: { value: "   " } });
      expect(confirmAdd(dialog)).toBeDisabled();
      fireEvent.click(confirmAdd(dialog));
      expect(addSpy).not.toHaveBeenCalled();

      fireEvent.change(prompt, { target: { value: addUnitContract.append.prompt } });
      fireEvent.click(confirmAdd(dialog));

      await waitFor(() => expect(addSpy).toHaveBeenCalledTimes(1));
      expect(addSpy.mock.calls[0]!.slice(0, 2)).toEqual(["proj", 1]);
      expect(addSpy.mock.calls[0]![2]).toStrictEqual(addUnitContract.append);
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(await screen.findByTestId("unit-row-E1U1")).toBeInTheDocument();
    });

    it("在选中单元之后插入：请求带上锚点，新单元排在锚点之后", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2")],
        unit_capabilities: {},
      });
      const addSpy = vi.spyOn(API, "addReferenceVideoUnit").mockResolvedValue({
        unit: mkUnit("E1U3", addUnitContract.insert_after.prompt),
        unit_capability: makeReferenceUnitCapability("E1U3"),
      });
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} hasScript />);
      await screen.findByTestId("unit-row-E1U1");

      fireEvent.click(screen.getByRole("button", { name: "在此后插入" }));
      const dialog = await addDialog();
      fireEvent.change(within(dialog).getByRole("textbox", { name: "正文" }), {
        target: { value: addUnitContract.insert_after.prompt },
      });
      fireEvent.click(confirmAdd(dialog));

      await waitFor(() => expect(addSpy).toHaveBeenCalledTimes(1));
      expect(addSpy.mock.calls[0]![2]).toStrictEqual(addUnitContract.insert_after);
      await waitFor(() =>
        expect(screen.getAllByTestId(/^unit-row-/).map((row) => row.dataset.testid)).toEqual([
          "unit-row-E1U1",
          "unit-row-E1U3",
          "unit-row-E1U2",
        ]),
      );
    });

    it("有未保存修改时只在提交新增时询问，取消新增保留修改", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
      const addSpy = vi.spyOn(API, "addReferenceVideoUnit").mockResolvedValue({
        unit: mkUnit("E1U2", addUnitContract.insert_after.prompt),
        unit_capability: makeReferenceUnitCapability("E1U2"),
      });
      const discard = vi.fn();
      function DirtyUnit() {
        useLeaveGuard({ dirty: true, save: async () => true, discard });
        return null;
      }
      render(<><DirtyUnit /><ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} hasScript /></>);
      await screen.findByTestId("unit-row-E1U1");

      fireEvent.click(screen.getByRole("button", { name: "在此后插入" }));
      fireEvent.click(within(await addDialog()).getByRole("button", { name: "取消" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(discard).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole("button", { name: "在此后插入" }));
      const dialog = await addDialog();
      fireEvent.change(within(dialog).getByRole("textbox", { name: "正文" }), {
        target: { value: addUnitContract.insert_after.prompt },
      });
      fireEvent.click(confirmAdd(dialog));
      expect(addSpy).not.toHaveBeenCalled();
      fireEvent.click(within(await screen.findByRole("alertdialog", { name: "有未保存的修改" })).getByRole("button", { name: "放弃修改" }));

      await waitFor(() => expect(addSpy).toHaveBeenCalledTimes(1));
      expect(discard).toHaveBeenCalledTimes(1);
    });

    it("waits for an in-flight creation before leaving the canvas", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [], unit_capabilities: {} });
      const created = {
        unit: mkUnit("E1U1", addUnitContract.append.prompt),
        unit_capability: makeReferenceUnitCapability("E1U1"),
      };
      const pending = createDeferred<typeof created>();
      const addSpy = vi.spyOn(API, "addReferenceVideoUnit").mockReturnValue(pending.promise);
      let leave: ReturnType<typeof useConfirmLeave> = () => {};
      function Navigation() {
        leave = useConfirmLeave();
        return null;
      }
      render(<><Navigation /><ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} hasScript /></>);
      fireEvent.click(await screen.findByRole("button", { name: "新增第一个视频单元" }));
      const dialog = await addDialog();
      fireEvent.change(within(dialog).getByRole("textbox", { name: "正文" }), {
        target: { value: addUnitContract.append.prompt },
      });
      fireEvent.click(confirmAdd(dialog));
      await waitFor(() => expect(addSpy).toHaveBeenCalledTimes(1));

      const left = vi.fn();
      act(() => leave(left));
      const leftBeforeCreation = left.mock.calls.length;
      await act(async () => { pending.resolve(created); });
      await waitFor(() => expect(left).toHaveBeenCalledTimes(1));
      expect(leftBeforeCreation).toBe(0);
      expect(await screen.findByTestId("unit-row-E1U1")).toBeInTheDocument();
    });

    it("新增失败时保留对话框与已填写的正文，并提示原因", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [], unit_capabilities: {} });
      vi.spyOn(API, "addReferenceVideoUnit").mockRejectedValue(new Error("混用了台词写法"));
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} hasScript />);

      fireEvent.click(await screen.findByRole("button", { name: "新增第一个视频单元" }));
      const dialog = await addDialog();
      fireEvent.change(within(dialog).getByRole("textbox", { name: "正文" }), { target: { value: "镜头1：推门" } });
      fireEvent.click(confirmAdd(dialog));

      await waitFor(() => expect(useAppStore.getState().toast?.text).toBe("新增视频单元失败：混用了台词写法"));
      expect(within(dialog).getByRole("textbox", { name: "正文" })).toHaveValue("镜头1：推门");
      expect(confirmAdd(dialog)).toBeEnabled();
    });

    it("取消对话框不发新增请求，再次打开时正文清空", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [], unit_capabilities: {} });
      const addSpy = vi.spyOn(API, "addReferenceVideoUnit");
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} hasScript />);

      fireEvent.click(await screen.findByRole("button", { name: "新增第一个视频单元" }));
      const dialog = await addDialog();
      fireEvent.change(within(dialog).getByRole("textbox", { name: "正文" }), { target: { value: "草稿" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

      fireEvent.click(screen.getByRole("button", { name: "新增第一个视频单元" }));
      expect(within(await addDialog()).getByRole("textbox", { name: "正文" })).toHaveValue("");
      expect(addSpy).not.toHaveBeenCalled();
    });
  });

  it("renders the script plan preview instead of the unit list on the plan view", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    vi.spyOn(API, "getScriptReview").mockResolvedValue({
      episode: 1,
      content_mode: "narration",
      status: "pending_review",
      fingerprint: "fp",
      confirmed_at: null,
      supported_durations: null,
      duration_tiers: null,
      episode_target_duration: null,
      script_overwrite: null,
      content: { units: [{ unit_id: "E1U1", text: "shot text", duration_seconds: 5, source_text: "" }] },
    });
    const { rerender } = render(<ReferenceVideoCanvas {...BOARD} view="plan" projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByText("shot text")).toBeInTheDocument());
    expect(screen.queryByTestId("unit-row-E1U1")).not.toBeInTheDocument();
    rerender(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
  });

  it("resets a stale selectedUnitId (e.g. from a previous episode) to the first unit of current units", async () => {
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

  it("flips the generate button to busy optimistically before the task poll picks it up", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
    let resolveGen: (v: { task_id: string; deduped: boolean }) => void = () => {};
    const genSpy = vi.spyOn(API, "generateReferenceVideoUnit").mockReturnValue(
      new Promise((resolve) => {
        resolveGen = resolve;
      }),
    );
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    const btn = await screen.findByRole("button", { name: UNIT_GENERATE_CTA });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    await waitFor(() => expect(genSpy).toHaveBeenCalled());
    resolveGen({ task_id: "t1", deduped: false });
    await waitFor(() => expect(screen.getByRole("button", { name: /^(Generating|生成中)/ })).toBeDisabled());
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toMatch(/Queued for generation|已加入生成队列/);
    });
  });

  it("重试失败 unit 后在真实任务行落库前即禁用按钮", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
    seedTaskState({
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

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generating|生成中/ })).toBeDisabled();
    });
    expect(screen.queryByText(/Generation failed|生成失败/)).not.toBeInTheDocument();
    resolveGen({ task_id: "t2", deduped: false });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generating|生成中/ })).toBeDisabled();
    });
    expect(screen.queryByText(/Generation failed|生成失败/)).not.toBeInTheDocument();
  });

  it("重新生成已完成 unit 后在真实任务行落库前即禁用按钮", async () => {
    const unit = mkUnit("E1U1");
    unit.generated_assets.video_clip = "videos/E1U1.mp4";
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit], unit_capabilities: {} });
    seedTaskState({
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

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generating|生成中/ })).toBeDisabled();
    });
    resolveGen({ task_id: "t2", deduped: false });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generating|生成中/ })).toBeDisabled();
    });
  });

  it("响应式占用信号尚未追上真实 store 时，生成提交仍被 getState() 新鲜读拦截", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
    const genSpy = vi
      .spyOn(API, "generateReferenceVideoUnit")
      .mockResolvedValue({ task_id: "t1", deduped: false } as never);
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

    const generate = await screen.findByRole("button", { name: UNIT_GENERATE_CTA });
    expect(generate).toBeEnabled();

    act(() => {
      seedTaskState({ tasks: [runningTask("E1U1")] as never });
    });

    fireEvent.click(generate);
    await waitFor(() => expect(pushToast).toHaveBeenCalled());
    expect(genSpy).not.toHaveBeenCalled();
  });

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

    act(() => {
      seedTaskState({ tasks: [runningTask("E1U1")] as never });
    });

    fireEvent.click(batch);
    await confirmBatchPreview();
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
    await confirmBatchPreview();

    await waitFor(() =>
      expect(batchSpy).toHaveBeenCalledWith("proj", 1, {
        unit_ids: ["E1U1", "E1U2"],
      }),
    );
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
    await confirmBatchPreview();

    await waitFor(() => expect(batchSpy).toHaveBeenCalled());
    expect(batchSpy).toHaveBeenCalledWith("proj", 1, { unit_ids: ["E1U1"] });
  });

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

    act(() => {
      seedTaskState({ tasks: [runningTask("E1U1")] as never });
    });

    fireEvent.change(input!, { target: { files: [new File(["x"], "clip.mp4", { type: "video/mp4" })] } });

    await waitFor(() => expect(pushToast).toHaveBeenCalled());
    expect(uploadSpy).not.toHaveBeenCalled();
  });

  it("批量生成跳过上传在途的 unit", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    vi.spyOn(API, "uploadReferenceUnitVideo").mockReturnValue(new Promise(() => {}) as never);
    const batchSpy = vi
      .spyOn(API, "generateReferenceVideoBatch")
      .mockResolvedValue(mkAdmission());
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    const { container } = render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    const batch = await screen.findByRole("button", { name: /Fill in videos|补齐视频/ });
    await waitFor(() => expect(batch).toBeEnabled());

    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    fireEvent.change(input!, { target: { files: [new File(["x"], "clip.mp4", { type: "video/mp4" })] } });

    fireEvent.click(batch);
    const dialog = within(await screen.findByRole("alertdialog"));
    expect(await dialog.findByText(/跳过 1 个视频单元|Skipped 1 video unit/)).toBeInTheDocument();
    expect(dialog.getByText(/正在上传视频|Uploading a video/)).toBeInTheDocument();
    expect(API.previewReferenceVideoBatch).toHaveBeenCalledWith("proj", 1, { unit_ids: ["E1U2"] }, expect.anything());
    await confirmBatchPreview();
    await waitFor(() => expect(batchSpy).toHaveBeenCalledTimes(1));
    expect(batchSpy).toHaveBeenCalledWith("proj", 1, {
      unit_ids: ["E1U2"],
    });
  });

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

      const confirm = await screen.findByRole("button", { name: CONFIRM_CTA });
      expect(genSpy).not.toHaveBeenCalled();
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

    it("从脚本规划点击超限提示会打开目标视频单元的文稿", async () => {
      seedFormalUnits(["E1U1"]);
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1", "需要调整的文稿")], unit_capabilities: {},
      });
      vi.spyOn(API, "getScriptReview").mockRejectedValue(new Error("no draft"));
      stubOverflowPrecheck();
      const { hook, searchHook } = memoryLocation({ path: "/episodes/1" });
      rtlRender(
        <Router hook={hook} searchHook={searchHook}>
          <LeaveGuardProvider>
            <EpisodePage
              projectName="proj"
              episode={1}
              projectData={{ ...STUB_PROJECT, generation_mode: "reference_video" }}
              script={useProjectsStore.getState().currentScripts["episode_1.json"]}
              demo
              onSaveTitle={async () => {}}
              onViewUnit={() => {}}
              renderCanvas={(props) => <ReferenceVideoCanvas {...props} projectName="proj" episode={1} />}
            />
          </LeaveGuardProvider>
        </Router>,
      );

      const edit = await overflowEditAction();
      await userEvent.click(screen.getByRole("tab", { name: /脚本规划|Script plan/ }));
      expect(screen.getByRole("tab", { name: /脚本规划|Script plan/, selected: true })).toBeInTheDocument();

      act(() => edit.onClick());

      expect(await screen.findByRole("combobox", { name: "视频单元提示词" })).toHaveValue("需要调整的文稿");
    });

    function stubOverflowPrecheck() {
      vi.spyOn(API, "precheckReferenceVideoDuration").mockRejectedValue(new ReferenceProjectionError({
        allowed: false,
        kind: "reference_request_projection",
        unit_id: "E1U1",
        problems: [{
          code: "video_reference_images_exceeded",
          blocking: true,
          unit_id: "E1U1",
          locations: [],
          params: {},
          action: "fix_input",
        }],
      }));
    }

    async function overflowEditAction() {
      fireEvent.click(await screen.findByRole("button", { name: UNIT_GENERATE_CTA }));
      return waitFor(() => {
        const action = useAppStore.getState().toast?.action;
        expect(action?.label).toMatch(/编辑 E1U1 的引用|Edit references for E1U1/);
        return action!;
      });
    }

    it.each([
      { transition: "切换项目", projectName: "other", episode: 1, waitsForSave: false },
      { transition: "切换集", projectName: "proj", episode: 2, waitsForSave: false },
      { transition: "确认保存期间切换集", projectName: "proj", episode: 2, waitsForSave: true },
    ])("$transition 后，旧提示不选中同 ID 的单元", async ({ projectName, episode, waitsForSave }) => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {},
      });
      stubOverflowPrecheck();
      const pendingSave = createDeferred<boolean>();
      const save = vi.fn(() => pendingSave.promise);
      function DirtyUnit() {
        useLeaveGuard({ dirty: true, save });
        return null;
      }
      const canvas = (project: string, episodeNumber: number) => (
        <>
          {waitsForSave && <DirtyUnit />}
          <ReferenceVideoCanvas
            key={referenceVideoCacheKey(project, episodeNumber)}
            {...BOARD}
            projectName={project}
            episode={episodeNumber}
          />
        </>
      );

      const { rerender } = render(canvas("proj", 1));
      const edit = await overflowEditAction();
      if (waitsForSave) {
        act(() => edit.onClick());
        fireEvent.click(await screen.findByRole("button", { name: /Save and switch|保存并切换/ }));
      }
      expect(save).toHaveBeenCalledTimes(waitsForSave ? 1 : 0);

      rerender(canvas(projectName, episode));
      await waitFor(() => expect(useReferenceVideoStore.getState().loading).toBe(false));
      act(() => useReferenceVideoStore.getState().select("E1U2"));

      if (waitsForSave) {
        await act(async () => pendingSave.resolve(true));
      } else {
        act(() => edit.onClick());
      }
      expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U2");
    });
  });

  describe("整批准入判定", () => {
    const BATCH_CONFIRM_CTA = /Generate at these lengths|按这些档位生成/;

    beforeEach(() => seedFormalUnits(["E1U1", "E1U2", "E1U3"]));

    async function clickBatch() {
      const batch = await screen.findByRole("button", { name: /Fill in videos|补齐视频/ });
      await waitFor(() => expect(batch).toBeEnabled());
      fireEvent.click(batch);
      return batch;
    }

    async function submitBatch() {
      await clickBatch();
      await confirmBatchPreview();
      await waitFor(() => expect(screen.queryByTestId("reference-batch-preview")).not.toBeInTheDocument());
    }

    it("点补齐视频先列出名单、跳过项与预估费用，取消后不提交", async () => {
      const done = mkUnit("E1U3");
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2"), done], unit_capabilities: {} });
      const previewSpy = vi.spyOn(API, "previewReferenceVideoBatch").mockResolvedValue(
        mkPreview({
          units: [
            { unit_id: "E1U1", admitted: true, problems: [] },
            { unit_id: "E1U2", admitted: true, problems: [] },
          ],
          skipped_unit_ids: ["E1U3"],
          estimated_cost: { USD: 1.6 },
          unpriced_count: 1,
        }),
      );
      const batchSpy = vi.spyOn(API, "generateReferenceVideoBatch");

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      await clickBatch();

      const dialog = within(await screen.findByRole("alertdialog"));
      expect(await dialog.findByText(/将生成 2 个视频单元|Will generate 2 video units/)).toBeInTheDocument();
      expect(dialog.getByRole("img", { name: "视频单元 1" })).toBeInTheDocument();
      expect(dialog.getByRole("img", { name: "视频单元 2" })).toBeInTheDocument();
      expect(dialog.getByText(/跳过 1 个视频单元|Skipped 1 video unit/)).toBeInTheDocument();
      expect(dialog.getByRole("img", { name: "视频单元 3" })).toBeInTheDocument();
      expect(dialog.getByText(/^(已有视频|Already has a video)$/)).toBeInTheDocument();
      expect(dialog.getByText(/预估费用：\$1\.60|Estimated cost: \$1\.60/)).toBeInTheDocument();
      expect(dialog.getByText(/另有 1 个视频单元未定价|1 more video unit has no price/)).toBeInTheDocument();
      expect(previewSpy).toHaveBeenCalledWith("proj", 1, { unit_ids: ["E1U1", "E1U2", "E1U3"] }, expect.anything());

      fireEvent.click(dialog.getByRole("button", { name: /Cancel|取消/ }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(batchSpy).not.toHaveBeenCalled();
    });

    it("预览失败时说明原因且不能提交", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
      vi.spyOn(API, "previewReferenceVideoBatch").mockRejectedValue(new Error("offline"));
      const batchSpy = vi.spyOn(API, "generateReferenceVideoBatch");

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      await clickBatch();

      const dialog = within(await screen.findByRole("alertdialog"));
      expect(await dialog.findByRole("alert")).toHaveTextContent(/无法规划这一批：offline|Couldn't plan this batch: offline/);
      expect(dialog.getByRole("button", { name: BATCH_START_CTA })).toBeDisabled();
      expect(batchSpy).not.toHaveBeenCalled();
    });

    it("混合报价列出超限单元的原因，确认只提交其余单元", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {},
      });
      const reason = "超出 1 张上限的参考图：花店";
      vi.spyOn(API, "previewReferenceVideoBatch").mockResolvedValue(mkPreview({
        units: [
          { unit_id: "E1U1", admitted: true, problems: [] },
          { unit_id: "E1U2", admitted: false, problems: [
            { code: "video_reference_images_exceeded", action: "fix_input", message: reason, params: {} },
          ] },
        ],
      }));
      const submit = vi.spyOn(API, "generateReferenceVideoBatch").mockResolvedValue(mkAdmission());

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      await clickBatch();
      const dialog = within(await screen.findByRole("alertdialog"));
      expect(await dialog.findByText(reason)).toBeInTheDocument();
      expect(dialog.getByRole("button", { name: /编辑 E1U2 的引用|Edit references for E1U2/ })).toBeEnabled();
      await confirmBatchPreview();
      await waitFor(() => expect(submit).toHaveBeenCalledWith("proj", 1, { unit_ids: ["E1U1"] }));
    });

    it("停在解析预览时，超限单元的编辑入口切回可编辑的文稿", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {},
      });
      vi.spyOn(API, "previewReferenceScript").mockResolvedValue({ utterances: [], warnings: [] });
      vi.spyOn(API, "previewReferenceVideoBatch").mockResolvedValue(mkPreview({
        units: [
          { unit_id: "E1U1", admitted: true, problems: [] },
          { unit_id: "E1U2", admitted: false, problems: [
            { code: "video_reference_images_exceeded", action: "fix_input", message: "超出 1 张上限的参考图：花店", params: {} },
          ] },
        ],
      }));

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      await screen.findByRole("combobox");
      fireEvent.click(await screen.findByRole("tab", { name: /Parse preview|解析预览/ }));
      expect(screen.queryByRole("combobox")).not.toBeInTheDocument();

      await clickBatch();
      const dialog = within(await screen.findByRole("alertdialog"));
      fireEvent.click(await dialog.findByRole("button", { name: /编辑 E1U2 的引用|Edit references for E1U2/ }));

      expect(await screen.findByRole("tab", { name: /^(Script|文稿)$/, selected: true })).toBeInTheDocument();
      expect(await screen.findByRole("combobox")).toBeInTheDocument();
    });

    it("需确认档位时在同一个确认框里拍板，确认一次就带 confirmed_request_durations 提交", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2"), mkUnit("E1U3")], unit_capabilities: {} });
      vi.spyOn(API, "previewReferenceVideoBatch").mockResolvedValue(
          mkPreview({
            decision: "confirmation_required",
            estimated_cost: { USD: 1.6 },
            unpriced_count: 1,
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
      );
      const batchSpy = vi
        .spyOn(API, "generateReferenceVideoBatch")
        .mockResolvedValueOnce(mkAdmission({ task_ids: ["t1", "t2", "t3"] }));

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      await clickBatch();

      const confirm = await screen.findByRole("button", { name: BATCH_CONFIRM_CTA });
      const dialog = within(screen.getByRole("alertdialog"));
      expect(dialog.getByText(/将生成 3 个视频单元|Will generate 3 video units/)).toBeInTheDocument();
      expect(dialog.getByText(/预估费用：\$1\.60|Estimated cost: \$1\.60/)).toBeInTheDocument();
      expect(dialog.getByText(/^(?:8 秒|8s)$/)).toBeInTheDocument();
      expect(dialog.getByText(/2 个单元|2 units/)).toBeInTheDocument();
      expect(dialog.getByText(/约 \$1\.60|about \$1\.60/)).toBeInTheDocument();
      expect(dialog.getByText(/报价不可用|price unavailable/)).toBeInTheDocument();
      for (const label of ["视频单元 1", "视频单元 2", "视频单元 3"]) {
        expect(dialog.getAllByRole("img", { name: label })).toHaveLength(2);
      }

      fireEvent.click(confirm);
      await waitFor(() => expect(batchSpy).toHaveBeenCalledTimes(1));
      expect(batchSpy).toHaveBeenLastCalledWith("proj", 1, {
        unit_ids: ["E1U1", "E1U2", "E1U3"],
        confirmed_request_durations: { E1U1: 8, E1U2: 8, E1U3: 4 },
      });
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    });

    it("提交时档位与预览不同，按新结论再确认一次后重发", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
      const batchSpy = vi
        .spyOn(API, "generateReferenceVideoBatch")
        .mockResolvedValueOnce(
          mkAdmission({
            decision: "confirmation_required",
            task_ids: [],
            units: [
              { unit_id: "E1U1", admitted: true, problems: [] },
              { unit_id: "E1U2", admitted: false, request_duration_seconds: 8, problems: [] },
            ],
            confirmation: {
              tiers: [
                { request_duration_seconds: 8, unit_count: 1, unit_ids: ["E1U2"], cost_amount: 0.8, cost_currency: "USD" },
              ],
            },
          }),
        )
        .mockResolvedValueOnce(mkAdmission({ task_ids: ["t1", "t2"] }));

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      await submitBatch();

      const confirm = await screen.findByRole("button", { name: BATCH_CONFIRM_CTA });
      expect(screen.getAllByRole("alertdialog")).toHaveLength(1);
      fireEvent.click(confirm);
      await waitFor(() => expect(batchSpy).toHaveBeenCalledTimes(2));
      expect(batchSpy).toHaveBeenLastCalledWith("proj", 1, {
        unit_ids: ["E1U1", "E1U2"],
        confirmed_request_durations: { E1U2: 8 },
      });
    });

    it("确认停留期间已完成的单元不再重发", async () => {
      const [u1, u2] = [mkUnit("E1U1"), mkUnit("E1U2")];
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [u1, u2], unit_capabilities: {} });
      vi.spyOn(API, "previewReferenceVideoBatch").mockResolvedValue(
          mkPreview({
            decision: "confirmation_required",
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
      );
      const batchSpy = vi.spyOn(API, "generateReferenceVideoBatch").mockResolvedValueOnce(mkAdmission());

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      await clickBatch();
      const confirm = await screen.findByRole("button", { name: BATCH_CONFIRM_CTA });

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
      await waitFor(() => expect(batchSpy).toHaveBeenCalledTimes(1));
      expect(batchSpy).toHaveBeenLastCalledWith("proj", 1, {
        unit_ids: ["E1U2"],
        confirmed_request_durations: { E1U2: 4 },
      });
    });

    it("受阻时在确认框里列出每个单元的缺口与下一步，并标明被连带扣下的单元，不能提交", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2"), mkUnit("E1U3")], unit_capabilities: {} });
      const batchSpy = vi.spyOn(API, "generateReferenceVideoBatch");
      vi.spyOn(API, "previewReferenceVideoBatch").mockResolvedValue(
        mkPreview({
          decision: "blocked",
          estimated_cost: { USD: 0.8 },
          units: [
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
      expect(dialog.getByText("引用的角色图缺失")).toBeInTheDocument();
      expect(dialog.getByText("该单元需要重新规划")).toBeInTheDocument();
      expect(dialog.getByText(/补上或更换缺失的参考素材|add or replace the missing reference/)).toBeInTheDocument();
      expect(dialog.getByText(/改写这个单元|rewrite this unit/)).toBeInTheDocument();
      expect(dialog.getByText(/^(?:申请 12 秒|requesting 12s)$/)).toBeInTheDocument();
      expect(dialog.getByText(/本身没问题|w(as|ere) fine but w(as|ere) held back/)).toBeInTheDocument();
      for (const label of ["视频单元 1", "视频单元 2", "视频单元 3"]) {
        expect(dialog.getByRole("img", { name: label })).toBeInTheDocument();
      }

      expect(dialog.queryByText(/预估费用|Estimated cost/)).not.toBeInTheDocument();
      expect(dialog.getByRole("button", { name: BATCH_START_CTA })).toBeDisabled();
      fireEvent.click(dialog.getByRole("button", { name: /Cancel|取消/ }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(batchSpy).not.toHaveBeenCalled();
    });

    it("入队中断时逐个列出没排上队列的单元与原因", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2"), mkUnit("E1U3")], unit_capabilities: {} });
      const batchSpy = vi.spyOn(API, "generateReferenceVideoBatch").mockResolvedValue(
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
      await submitBatch();

      const dialog = within(await screen.findByRole("alertdialog"));
      expect(dialog.getByRole("img", { name: "视频单元 2" })).toBeInTheDocument();
      expect(dialog.getByRole("img", { name: "视频单元 3" })).toBeInTheDocument();
      expect(dialog.getByText("队列暂时不可用")).toBeInTheDocument();
      expect(dialog.getByText("任务创建失败")).toBeInTheDocument();
      expect(dialog.queryByRole("img", { name: "视频单元 1" })).not.toBeInTheDocument();
      expect(
        dialog.getByText(/再次批量生成就会补上|generate the batch again/),
      ).toBeInTheDocument();
      expect(
        dialog.getByText(/已有视频，本次跳过|already had a video and is skipped/),
      ).toBeInTheDocument();
      await waitFor(() => {
        expect(useAppStore.getState().toast?.text).toMatch(/入队中断|Enqueue was interrupted/);
      });

      fireEvent.click(dialog.getByRole("button", { name: /Got it|知道了/ }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(batchSpy).toHaveBeenCalledTimes(1);
    });

    it("首个目标就中断时不说成部分排上", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
      vi.spyOn(API, "generateReferenceVideoBatch").mockResolvedValue(
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
      await submitBatch();

      const dialog = within(await screen.findByRole("alertdialog"));
      expect(dialog.getByRole("img", { name: "视频单元 1" })).toBeInTheDocument();
      expect(dialog.getByRole("img", { name: "视频单元 2" })).toBeInTheDocument();
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
      await submitBatch();

      await waitFor(() => {
        expect(useAppStore.getState().toast?.text).toMatch(/批量生成请求失败|Batch generation request failed/);
      });
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
  });

  it("没有待生成 unit 时批量生成按钮禁用", async () => {
    const ready = mkUnit("E1U1");
    ready.generated_assets.video_clip = "videos/E1U1.mp4";
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [ready], unit_capabilities: {} });

    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

    const batch = await screen.findByRole("button", { name: /Fill in videos|补齐视频/ });
    await waitFor(() => expect(batch).toBeDisabled());
  });


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

  it("有未保存修改时定位到单元只询问一次：继续编辑什么都不变，放弃后切到视频单元视图并选中", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    vi.spyOn(API, "getScriptReview").mockRejectedValue(new Error("not needed"));
    const saveNothing = async () => true;
    function OtherDirtyUnit() {
      useLeaveGuard({ dirty: true, save: saveNothing });
      return null;
    }
    const onViewChange = vi.fn();
    render(<><OtherDirtyUnit /><ReferenceVideoCanvas view="plan" onViewChange={onViewChange} projectName="proj" episode={1} /></>);
    await waitFor(() => expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U1"));

    act(() => useAppStore.getState().triggerScrollTo({ type: "reference_unit", id: "E1U2", route: "/episodes/1" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "继续编辑" }));
    expect(onViewChange).not.toHaveBeenCalled();
    expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U1");

    act(() => useAppStore.getState().triggerScrollTo({ type: "reference_unit", id: "E1U2", route: "/episodes/1" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "放弃修改" }));
    expect(onViewChange).toHaveBeenCalledWith("board", { replace: true });
    expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U2");
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
    expect(useAppStore.getState().scrollTarget?.id).toBe("E1U2");
    await act(async () => {
      resolveList({ units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    });
    await waitFor(() => expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U2"));
    expect(useAppStore.getState().scrollTarget).toBeNull();
  });

  it("clears an unresolvable reference_unit target after expiry without further updates", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    await waitFor(() => expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U1"));
    act(() => {
      useAppStore.getState().triggerScrollTo({
        type: "reference_unit",
        id: "E9U9",
        route: "/episodes/1",
        expires_at: Date.now() + 200,
      });
    });
    expect(useAppStore.getState().scrollTarget?.id).toBe("E9U9");
    await waitFor(() => expect(useAppStore.getState().scrollTarget).toBeNull());
  });

  it("参考生视频分组失效信号自增时重拉分组，展示新落地的成片", async () => {
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
          character_voice_binding: "reference_audio",
          characters: { 王: { description: "", voice_updated_at: VOICE_UPDATED_AT } },
        },
        refreshProject: vi.fn().mockResolvedValue({ status: "ok" }),
      } as never);
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [staleUnit()], unit_capabilities: {} });
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    }

    it("关闭时写回角色自己的 voice_updated_at，而不是本机当前时间", async () => {
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

      await screen.findAllByRole("img", { name: "视频单元 1" });
      expect(screen.queryByRole("button", { name: /Got it|知道了/ })).not.toBeInTheDocument();
    });
  });

  describe("提示词编写部分完成闭环", () => {
    const ACCEPTED_AUTHORING = {
      batch: { batch_id: "b-retry", members: [{ unit_id: "episode-1", task_id: "t-retry", deduped: false }] },
    } as unknown as Awaited<ReturnType<typeof API.authorPrompts>>;

    async function loadPersistedTasks(tasks: ReturnType<typeof makeTask>[]) {
      vi.spyOn(API, "getActiveTasks").mockResolvedValue({
        items: tasks.filter((item) => item.status === "queued" || item.status === "running"),
        truncated: false,
      });
      vi.spyOn(API, "listTasks").mockResolvedValue({
        items: tasks,
        total: tasks.length,
        page: 1,
        page_size: 200,
      });
      vi.spyOn(API, "getTaskStats").mockResolvedValue({ stats: defaultTaskStats });
      useTasksStore.getState().setRefreshScope({ projectName: "proj" });
      await act(async () => {
        await useTasksStore.getState().refreshTasks();
      });
    }

    afterEach(() => {
      useTasksStore.getState().setRefreshScope(null);
      useAssistantStore.setState(useAssistantStore.getInitialState(), true);
    });

    it("加载、重进页面读回持久 partial 任务与重试全程零 /drafts 请求，默认补缺失败单元保持待编写并支持一键重试、编辑与交 Agent", async () => {
      const writtenUnit: ReferenceVideoUnit = { ...mkUnit("E1U1", "已补全的镜头一提示词"), pending_authoring: false };
      const failedUnit: ReferenceVideoUnit = { ...mkUnit("E1U2", "原始大纲占位"), pending_authoring: true };
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [writtenUnit, failedUnit],
        unit_capabilities: {},
      });
      useProjectsStore.setState({
        currentProjectName: "proj",
        currentProjectData: {
          ...STUB_PROJECT,
          episodes: [{ episode: 1, title: "雨夜开场", script_file: "episode_1.json" }],
        },
        currentScripts: {
          "episode_1.json": { episode: 1, video_units: [writtenUnit, failedUnit] } as unknown as EpisodeScript,
        },
      });
      const partialTask = makeTask({
        task_id: "t-partial-fill",
        project_name: "proj",
        task_type: "text_episode_script",
        resource_id: "episode-1",
        status: "succeeded",
        payload: { episode: 1, rewrite: false, instructions: "保持冷色调" },
        result: {
          message: "部分完成",
          outcome: "partial",
          written_ids: ["E1U1"],
          failed_units: [
            {
              unit_id: "E1U2",
              code: "dialogue_mismatch",
              message: "unit E1U2 台词被改动；请用 entry_ids 点名重试（重写需重新认可覆盖），或用 patch_episode_script 修改",
              abort_reason: "formal_revision_conflict: 正式脚本已变化，已停止写回",
            },
          ],
        },
        updated_at: "2026-11-24T10:00:00Z",
      });
      const authorSpy = vi.spyOn(API, "authorPrompts").mockResolvedValue(ACCEPTED_AUTHORING);

      await loadPersistedTasks([partialTask]);
      const firstMount = render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

      const notice = await screen.findByRole("status", { name: "提示词编写部分完成" });
      expect(notice).toHaveTextContent("已写回 1 个单元，1 个单元未写回。");
      expect(notice).toHaveTextContent("已写回：视频单元 1");
      expect(notice).toHaveTextContent("视频单元 2 台词被改动");
      expect(notice).not.toHaveTextContent("entry_ids");
      expect(notice).toHaveTextContent("中止原因：正式脚本已变化，已停止写回");
      expect(within(notice).getByText("待编写")).toBeInTheDocument();

      expect(within(screen.getByTestId("unit-row-E1U1")).queryByText("待编写")).not.toBeInTheDocument();
      expect(within(screen.getByTestId("unit-row-E1U2")).getByText("待编写")).toBeInTheDocument();

      fireEvent.click(within(notice).getByRole("button", { name: "编辑" }));
      await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("原始大纲占位"));
      expect(screen.getByText(/^待编写：/)).toBeInTheDocument();

      fireEvent.click(within(notice).getByRole("button", { name: "重试失败单元" }));
      await waitFor(() =>
        expect(authorSpy).toHaveBeenCalledWith("proj", 1, {
          entry_ids: ["E1U2"],
          rewrite: false,
          instructions: "保持冷色调",
          overwrite_revision: null,
        }),
      );

      fireEvent.click(within(notice).getByRole("button", { name: "交给 Agent" }));
      expect(useAssistantStore.getState().input).toContain("E1U2");
      expect(useAssistantStore.getState().input).toContain("patch_episode_script");
      expect(useAppStore.getState().assistantPanelOpen).toBe(true);

      firstMount.unmount();
      const newerTasks = Array.from({ length: 200 }, (_, index) =>
        makeTask({ task_id: `newer-${index}`, project_name: "proj", task_type: "video" }),
      );
      await loadPersistedTasks(newerTasks);
      vi.mocked(API.listTasks).mockImplementation(async (filters) => {
        const items = filters?.projectName === "proj" && filters.resourceId === "episode-1" && filters.taskType === "text_episode_script"
          ? [partialTask] : newerTasks;
        return { items, total: items.length, page: 1, page_size: items.length };
      });
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      expect(await screen.findByRole("status", { name: "提示词编写部分完成" })).toHaveTextContent(
        "视频单元 2 台词被改动",
      );

    });

    it("显式重写部分失败时保留已编写旧正文与已编写状态，点名重试需重新确认覆盖范围后才携带 revision 重试", async () => {
      const rewrittenUnit: ReferenceVideoUnit = { ...mkUnit("E1U1", "重写后的镜头一"), pending_authoring: false };
      const keptUnit: ReferenceVideoUnit = { ...mkUnit("E1U2", "旧的已编写正文不可丢失"), pending_authoring: false };
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [rewrittenUnit, keptUnit],
        unit_capabilities: {},
      });
      const partialRewriteTask = makeTask({
        task_id: "t-partial-rewrite",
        project_name: "proj",
        task_type: "text_episode_script",
        resource_id: "episode-1",
        status: "succeeded",
        payload: { episode: 1, rewrite: true, instructions: "加强光影对比" },
        result: {
          message: "部分完成",
          outcome: "partial",
          written_ids: ["E1U1"],
          failed_units: [
            {
              unit_id: "E1U2",
              code: "missing_unit",
              message: "模型漏返单元；请用 entry_ids 点名重试（重写需重新认可覆盖），或用 patch_episode_script 修改",
              abort_reason: "RuntimeError: provider timeout",
            },
          ],
        },
        updated_at: "2026-11-24T10:05:00Z",
      });
      const overwrite: PromptOverwrite = {
        revision: "rev-overwrite-2",
        entries: [{ id: "E1U2", fields: ["text"] }],
        text: "将覆盖 E1U2 的正文。",
      };
      const authorSpy = vi
        .spyOn(API, "authorPrompts")
        .mockRejectedValueOnce(new ApiRequestError("需要确认覆盖", { prompt_overwrite: overwrite }, 409))
        .mockResolvedValueOnce(ACCEPTED_AUTHORING);

      await loadPersistedTasks([partialRewriteTask]);
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

      const notice = await screen.findByRole("status", { name: "提示词编写部分完成" });
      expect(notice).toHaveTextContent("已编写的失败单元保留了原正文与已编写状态");
      expect(notice).toHaveTextContent("模型漏返单元");
      expect(notice).toHaveTextContent("RuntimeError: provider timeout");
      expect(within(notice).getByText("已编写")).toBeInTheDocument();
      expect(within(screen.getByTestId("unit-row-E1U2")).queryByText("待编写")).not.toBeInTheDocument();

      fireEvent.click(within(notice).getByRole("button", { name: "编辑" }));
      await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("旧的已编写正文不可丢失"));
      expect(screen.queryByText(/^待编写：/)).not.toBeInTheDocument();

      fireEvent.click(within(notice).getByRole("button", { name: "点名重写失败单元" }));
      const confirmDialog = await screen.findByRole("alertdialog", { name: "确认覆盖已有提示词" });
      expect(within(confirmDialog).getByText("将覆盖 视频单元 2 的正文。")).toBeInTheDocument();
      expect(authorSpy).toHaveBeenCalledWith("proj", 1, {
        entry_ids: ["E1U2"],
        rewrite: true,
        instructions: "加强光影对比",
        overwrite_revision: null,
      });

      const confirmButton = within(confirmDialog).getByRole("button", { name: "AI 重写" });
      let handle: ReturnType<ReturnType<typeof useTasksStore.getState>["beginOptimisticActive"]> | null = null;
      act(() => {
        handle = useTasksStore
          .getState()
          .beginOptimisticActive("proj", "text_episode_script", "episode-1", "text_episode_script");
      });
      await waitFor(() => expect(confirmButton).toBeDisabled());

      act(() => {
        handle?.rollback();
      });
      await waitFor(() => expect(confirmButton).toBeEnabled());

      fireEvent.click(confirmButton);
      await waitFor(() =>
        expect(authorSpy).toHaveBeenLastCalledWith("proj", 1, {
          entry_ids: ["E1U2"],
          rewrite: true,
          instructions: "加强光影对比",
          overwrite_revision: "rev-overwrite-2",
        }),
      );
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());

    });

    it("后续提示词编写完成（outcome=complete）后自动收起旧的部分完成提示", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1", "镜头一"), mkUnit("E1U2", "镜头二")],
        unit_capabilities: {},
      });
      const partialTask = makeTask({
        task_id: "t-partial-1",
        project_name: "proj",
        task_type: "text_episode_script",
        resource_id: "episode-1",
        status: "succeeded",
        result: {
          message: "部分完成",
          outcome: "partial",
          written_ids: ["E1U1"],
          failed_units: [{ unit_id: "E1U2", code: "missing_unit", message: "模型漏返单元" }],
        },
        updated_at: "2026-11-24T10:00:00Z",
      });
      await loadPersistedTasks([partialTask]);
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      expect(await screen.findByRole("status", { name: "提示词编写部分完成" })).toBeInTheDocument();

      const completeTask = makeTask({
        task_id: "t-complete-2",
        project_name: "proj",
        task_type: "text_episode_script",
        resource_id: "episode-1",
        status: "succeeded",
        result: {
          message: "生成完成",
          outcome: "complete",
          written_ids: ["E1U2"],
          failed_units: [],
        },
        updated_at: "2026-11-24T10:10:00Z",
      });
      await loadPersistedTasks([completeTask, partialTask]);

      await waitFor(() =>
        expect(screen.queryByRole("status", { name: "提示词编写部分完成" })).not.toBeInTheDocument(),
      );
    });

    it("编辑单元有未保存修改时，部分完成重试先保存再入队，保存失败不发起付费重试", async () => {
      const pendingUnit: ReferenceVideoUnit = { ...mkUnit("E1U2", "旧失败正文"), pending_authoring: true };
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1", "已完成单元"), pendingUnit],
        unit_capabilities: {},
      });
      const partialTask = makeTask({
        task_id: "t-partial-save-first",
        project_name: "proj",
        task_type: "text_episode_script",
        resource_id: "episode-1",
        status: "succeeded",
        payload: { episode: 1, rewrite: false },
        result: {
          message: "部分完成",
          outcome: "partial",
          written_ids: ["E1U1"],
          failed_units: [{ unit_id: "E1U2", code: "missing_unit", message: "模型漏返单元" }],
        },
        updated_at: "2026-11-24T10:00:00Z",
      });
      const patchSpy = vi
        .spyOn(API, "patchReferenceVideoUnit")
        .mockRejectedValueOnce(new Error("保存失败"))
        .mockResolvedValueOnce({
          unit: { ...pendingUnit, text: "用户修正后的正文" },
          unit_capability: makeReferenceUnitCapability("E1U2"),
        });
      const authorSpy = vi.spyOn(API, "authorPrompts").mockResolvedValue(ACCEPTED_AUTHORING);

      await loadPersistedTasks([partialTask]);
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

      const notice = await screen.findByRole("status", { name: "提示词编写部分完成" });
      fireEvent.click(within(notice).getByRole("button", { name: "编辑" }));
      const editor = await screen.findByRole("combobox");
      await waitFor(() => expect(editor).toHaveValue("旧失败正文"));

      fireEvent.change(editor, { target: { value: "用户修正后的正文" } });
      fireEvent.click(within(notice).getByRole("button", { name: "重试失败单元" }));

      await waitFor(() => expect(patchSpy).toHaveBeenCalledTimes(1));
      expect(authorSpy).not.toHaveBeenCalled();
      const retryButton = within(notice).getByRole("button", { name: "重试失败单元" });
      await waitFor(() => expect(retryButton).toBeEnabled());

      fireEvent.click(retryButton);
      await waitFor(() => expect(patchSpy).toHaveBeenCalledTimes(2));
      await waitFor(() =>
        expect(authorSpy).toHaveBeenCalledWith("proj", 1, {
          entry_ids: ["E1U2"],
          rewrite: false,
          instructions: null,
          overwrite_revision: null,
        }),
      );
      expect(patchSpy.mock.invocationCallOrder[1]).toBeLessThan(authorSpy.mock.invocationCallOrder[0]);
    });

    it("观察到较新的完整任务后清除旧归档缓存，后续完整任务被挤出且归档查询失败时不再回退展示过期部分完成提示", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1", "镜头一"), mkUnit("E1U2", "镜头二")],
        unit_capabilities: {},
      });
      const partialTask = makeTask({
        task_id: "t-archived-partial",
        project_name: "proj",
        task_type: "text_episode_script",
        resource_id: "episode-1",
        status: "succeeded",
        result: {
          message: "部分完成",
          outcome: "partial",
          written_ids: ["E1U1"],
          failed_units: [{ unit_id: "E1U2", code: "missing_unit", message: "模型漏返单元" }],
        },
        updated_at: "2026-11-24T10:00:00Z",
      });
      const completeTask = makeTask({
        task_id: "t-recent-complete",
        project_name: "proj",
        task_type: "text_episode_script",
        resource_id: "episode-1",
        status: "succeeded",
        result: {
          message: "生成完成",
          outcome: "complete",
          written_ids: ["E1U2"],
          failed_units: [],
        },
        updated_at: "2026-11-24T10:10:00Z",
      });
      const fillerBatch1 = Array.from({ length: 200 }, (_, index) =>
        makeTask({ task_id: `filler-1-${index}`, project_name: "proj", task_type: "video" }),
      );
      const fillerBatch2 = Array.from({ length: 200 }, (_, index) =>
        makeTask({ task_id: `filler-2-${index}`, project_name: "proj", task_type: "video" }),
      );

      await loadPersistedTasks(fillerBatch1);
      vi.mocked(API.listTasks).mockImplementation(async (filters) => {
        if (filters?.projectName === "proj" && filters.resourceId === "episode-1" && filters.taskType === "text_episode_script") {
          return { items: [partialTask], total: 1, page: 1, page_size: 1 };
        }
        return { items: fillerBatch1, total: fillerBatch1.length, page: 1, page_size: fillerBatch1.length };
      });

      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
      expect(await screen.findByRole("status", { name: "提示词编写部分完成" })).toBeInTheDocument();

      await loadPersistedTasks([completeTask, ...fillerBatch1.slice(0, 199)]);
      await waitFor(() =>
        expect(screen.queryByRole("status", { name: "提示词编写部分完成" })).not.toBeInTheDocument(),
      );

      vi.mocked(API.listTasks).mockImplementation(async (filters) => {
        if (filters?.resourceId === "episode-1" && filters.taskType === "text_episode_script") {
          throw new Error("archive lookup failed");
        }
        return { items: fillerBatch2, total: fillerBatch2.length, page: 1, page_size: fillerBatch2.length };
      });
      await act(async () => {
        await useTasksStore.getState().refreshTasks();
      });

      await waitFor(() => expect(useAppStore.getState().toast?.tone).toBe("error"));
      expect(screen.queryByRole("status", { name: "提示词编写部分完成" })).not.toBeInTheDocument();
    });
  });
});
