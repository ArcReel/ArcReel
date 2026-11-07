import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render as rtlRender, screen, waitFor, fireEvent, act, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { Router, useLocation } from "wouter";
import { LeaveGuardProvider, useConfirmLeave, useLeaveGuard } from "@/components/shared/edit-unit/LeaveGuard";
import { EpisodeViewFactsProvider } from "@/components/canvas/episode-page/EpisodeViewScope";
import type { EpisodeViewFacts } from "@/components/canvas/episode-page/episode-view";
import { ReferenceVideoCanvas } from "./ReferenceVideoCanvas";
import { useReferenceVideoStore, referenceVideoCacheKey } from "@/stores/reference-video-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useActiveResourceIds, useLatestTasksByResource, useTasksStore } from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";
import { useCostStore } from "@/stores/cost-store";
import { API } from "@/api";
import { makeReferenceUnitCapability } from "@/test/factories";
import addUnitContract from "@/test/contracts/reference-unit-add-requests.json";
import { createDeferred } from "@/test/deferred";
import type {
  ReferenceDurationPrecheck,
  ReferenceUnitCapabilityMap,
  ReferenceVideoUnit,
  UnitGeneratedAssets,
} from "@/types";
import type { EpisodeScript, ProjectData } from "@/types";

// useActiveResourceIds / useLatestTasksByResource wrap real implementations by default; individual cases
// freeze returned signals to simulate reactive state lagging actual stores, proving submit handlers independently
// use fresh getState() reads.
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

/** Server per-unit bucket decisions: default no references → i2v tiers 3/8, overridable per case. */

/** Batch-admission outcome skeleton: always 200, decision carries outcomes. */
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

/** Batch-preview skeleton defaults to allowing all requested units without quotes. */
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

/** Batch-confirmation primary button: start generation when no tier decisions are needed. */
const BATCH_START_CTA = /^(Start generating|开始生成)$/;

/** Submit through batch-confirmation primary buttons. */
async function confirmBatchPreview(name: RegExp = BATCH_START_CTA) {
  const confirm = await screen.findByRole("button", { name });
  await waitFor(() => expect(confirm).toBeEnabled());
  fireEvent.click(confirm);
}

// Unit-preview generation CTA: anchor starts to exclude batch fill-videos entries. Both include
// generate-video wording; otherwise DOM order matches batches first, testing the wrong submission path.
const UNIT_GENERATE_CTA = /^(Generate video|生成视频)/;

/** Like app roots, place canvases under leave guards for dirty edits. */
function render(ui: ReactElement) {
  return rtlRender(ui, { wrapper: LeaveGuardProvider });
}

/** Open duration selects and return each option's seconds. */
async function openDurationOptions(): Promise<number[]> {
  await userEvent.click(await screen.findByRole("combobox", { name: /Duration|时长/ }));
  return (await screen.findAllByRole("option")).map((option) => Number.parseInt(option.textContent ?? "", 10));
}

/** Episode routing supplies canvas views, defaulting to video units. */
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

/** Formal project video units determine positions outside episode pages, e.g. admission outcomes/draft panels. */
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
    // Enqueue actions write optimistic marks into shared store instances across tests; reset them too.
    useTasksStore.setState({
      tasks: [],
      connected: false,
      optimisticActive: new Set(),
      optimisticActiveScriptFile: new Set(),
    });
    useAppStore.setState({ toast: null });
    // Duration preflight defaults to matching requested-duration bases, so generation needs no confirmation.
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
    // Batch previews default to allowing all requested units; outcome-focused cases simply confirm.
    vi.spyOn(API, "previewReferenceVideoBatch").mockImplementation(async (_project, _episode, payload) =>
      mkPreview({
        units: (payload.unit_ids ?? []).map((unitId) => ({ unit_id: unitId, admitted: true, problems: [] })),
      }),
    );
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
    seedFormalUnits(["E1U1", "E1U2"]);
    const save = vi
      .spyOn(API, "saveEpisodeDraft")
      .mockResolvedValue({ episode: 1, doc_type: "reference_prompt_authoring", adopted: true, draft: null });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

    expect(await screen.findByText("视频单元 2 使用了全角花括号")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "视频单元 2 · 1" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "视频单元 2 正文" }), { target: { value: "镜头二" } });
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
    // Unit headers show position / total, never entry IDs.
    expect(await screen.findByRole("button", { name: "视频单元 1 / 2" })).toHaveTextContent("1 / 2");
  });

  it("renders units that carry no generated_assets section at all", async () => {
    // Backends write this section only during generation; never-generated units lack it, so reads always allow absence.
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
      useTasksStore.setState({ tasks: [runningTask("E1U1")] as never });
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
      // Removal dialogs remain open, hiding editors from accessibility trees; query by value.
      expect(screen.getByDisplayValue("unsaved edit")).toBeInTheDocument();
    });

    it("询问未保存修改期间单元变为占用：提示占用，不移除，修改保留", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: UNITS, unit_capabilities: {} });
      const deleteSpy = vi.spyOn(API, "deleteReferenceVideoUnit").mockResolvedValue(undefined);
      const leaveDialog = await editThenConfirmRemoval();

      act(() => {
        useTasksStore.setState({ tasks: [runningTask("E1U1")] as never });
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
    useTasksStore.setState({ tasks: [{ ...runningTask("E1U1"), task_type: "tts" }] as never });
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

  it("外部提示词草稿到达时保留正在编辑的单元，放弃后才显示草稿", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1", "正式正文")], unit_capabilities: {} });
    const listDrafts = vi.spyOn(API, "listEpisodeDrafts").mockResolvedValue({ episode: 1, drafts: [] });
    seedFormalUnits(["E1U1"]);
    vi.spyOn(API, "getEpisodeDraft").mockResolvedValue({ episode: 1, doc_type: "reference_prompt_authoring", revision: "rev-2", editable_by: "user", content: { title: "第一集", units: [{ text: "新的提示词草稿" }] }, violations: [{ code: "fullwidth_braces", label: "unit E1U1", message: "草稿需要修复", line: 0, item_index: 0 }], soft_violations: [], formal_exists: true, item_ids: ["E1U1"] });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    fireEvent.change(await screen.findByRole("combobox"), { target: { value: "保留单元修改" } });
    listDrafts.mockResolvedValue({ episode: 1, drafts: [{ doc_type: "reference_prompt_authoring", editable_by: "user", violation_count: 1 }] });
    act(() => useAppStore.getState().invalidateEntities(["draft:episode_1_prompt_authoring"]));
    expect(await screen.findByText(/本集有新的提示词草稿/)).toHaveAttribute("role", "status");
    expect(screen.getByRole("combobox")).toHaveValue("保留单元修改");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "放弃修改" }));
    expect(await screen.findByRole("textbox", { name: "视频单元 1 正文" })).toHaveValue("新的提示词草稿");
  });

  it("auto-selects first unit on load and shows preview generate button", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generate video|生成视频/ })).toBeInTheDocument();
    });
  });

  // Parse/body views share editor columns; switching replaces textarea with read-only derivations.
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

  // Read-only parse panels lack focusable descendants; panels themselves need focus for keyboard access below the fold.
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

  // `out["__proto__"] = kind` on ordinary objects invokes inherited setters without own properties;
  // registered `__proto__` characters would look unregistered in highlights despite normal backend parsing.
  it("resolves an asset named __proto__ in the highlight lookup", async () => {
    useProjectsStore.setState({
      currentProjectName: "proj",
      currentProjectData: {
        ...STUB_PROJECT,
        // Computed keys: literal `__proto__:` specially sets prototypes, never own properties.
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

    // Inspect parse-panel highlights only, excluding duplicate text in unit-list cards.
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

  // Body is sole truth: saving sends editor text verbatim as PATCH prompt,
  // without independent reference-image fields.
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
    // jsdom lacks layout; vertically place rectangles by list positions so keyboard dragging can calculate destinations.
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
    // Overlays stop arrow bubbling; handle focus must permit sorting.
    await userEvent.keyboard("{ArrowUp}");
    await waitFor(() => expect(screen.getByText(/「视频单元 2」移到第 1 项/)).toBeInTheDocument());
    await userEvent.keyboard(" ");

    await waitFor(() => expect(moveSpy).toHaveBeenCalledWith("proj", 1, "E1U2", null));
  });

  describe("正文编辑单元", () => {
    /** Navigate under leave guards; `base` simulates nested project-route prefixes. */
    let navigate: (to: string) => void = () => {};
    function Navigator() {
      [, navigate] = useLocation();
      return null;
    }
    /** Script-bearing reference-to-video episodes default to units, with planning/editing also available. */
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

      // With scripts, units are default; removing view retains current view.
      act(() => navigate(path));
      expect(window.location.pathname + window.location.search).toBe(`${base}${path}`);
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

      act(() => navigate(`${path}?view=edit`));
      expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
      expect(window.location.search).toBe("");
    });
  });

  // Unregistered `@[名称]` is informational, never affecting save/generate availability.
  it("keeps saving and generating available when the body mentions an unregistered name", async () => {
    const unit = mkUnit("E1U1", "推门。");
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit], unit_capabilities: {} });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    const ta = await screen.findByRole("combobox");
    fireEvent.change(ta, { target: { value: "@[查无此人] 推门而入。" } });

    expect(await screen.findByRole("button", { name: /^(Save|保存)$/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /^(Save and generate|保存并生成)$/ })).toBeEnabled();
  });

  // Duration is unit-level truth: server per-unit tiers select separately persisted PATCH values, independent of body drafts.
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

    // Closing tabs requests browser-native dirty-edit confirmation through leave guards.
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

  // Reference-to-video prices requested seconds, so tier changes update estimates. SSE eventually syncs groups, but costs
  // refresh immediately on local write success rather than awaiting event round trips.
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

  // Narrowed tiers after model changes retain saved out-of-range options, preventing silent rewriting.
  it("keeps an out-of-slot saved duration as an option", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1")],
      unit_capabilities: { E1U1: makeReferenceUnitCapability("E1U1", { allowed_durations: [4, 8] }) },
    });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    expect(await screen.findByRole("combobox", { name: /Duration|时长/ })).toHaveTextContent(/^3/);
    expect(await openDurationOptions()).toEqual([3, 4, 8]);
  });

  // Servers use usable references: registered characters without images resolve i2v;
  // canvases keep i2v tiers instead of inferring r2v from registration alone.
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
    // Structured divergence explicitly names missing references/bucket changes, never silent switches.
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

  // Null server bucket tiers mean read-only controls, never fallback to other buckets.
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

  // Empty bodies are unexecutable and servers reject creation with needs_replan; fill before submission.
  // Compare request bodies verbatim with shared contracts; backend route tests validate the same files.
  describe("新增视频单元", () => {
    // Whole-screen accessible-name queries are slow on large DOMs; find the unique dialog by role, then inspect its heading.
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

  // Routing supplies views: planning hides UnitList, switching to units retains the list.
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
    // Hide UnitList and display structured script_plan intermediates through read-only highlighted preview panels.
    await waitFor(() => expect(screen.getByText("shot text")).toBeInTheDocument());
    expect(screen.queryByTestId("unit-row-E1U1")).not.toBeInTheDocument();
    rerender(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByTestId("unit-row-E1U1")).toBeInTheDocument());
  });

  // Default-select first units, avoiding existing units with editor placeholders.
  it("resets a stale selectedUnitId (e.g. from a previous episode) to the first unit of current units", async () => {
    // Simulate stale selectedUnitId after episode changes.
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

  // Without prompt_authoring scripts (segmented only), units endpoints have nothing to split and return 404; skip requests
  // rather than showing initial errors, fetching once scripts arrive.
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

  // Optimistic state must immediately mark busy during 3s queue-poll windows, or users
  // assume clicks failed and repeatedly enqueue.
  it("flips the generate button to busy optimistically before the task poll picks it up", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
    // Deferred promises simulate pending 202 responses.
    let resolveGen: (v: { task_id: string; deduped: boolean }) => void = () => {};
    const genSpy = vi.spyOn(API, "generateReferenceVideoUnit").mockReturnValue(
      new Promise((resolve) => {
        resolveGen = resolve;
      }),
    );
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    const btn = await screen.findByRole("button", { name: UNIT_GENERATE_CTA });
    // Before clicks, task stores are empty/buttons enabled.
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    await waitFor(() => expect(genSpy).toHaveBeenCalled());
    // After successful 202 but before task polls, action-layer optimistic marks immediately show
    // busy and "Generating…/生成中"; backend dedup indexes guard in-flight double clicks.
    resolveGen({ task_id: "t1", deduped: false });
    await waitFor(() => expect(screen.getByRole("button", { name: /^(Generating|生成中)/ })).toBeDisabled());
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toMatch(/Queued for generation|已加入生成队列/);
    });
  });

  // Retries retain failed rows, disabling optimistic statusMap (!queueRow); directly read occupancy
  // or repeated clicks can enqueue between requests and row persistence.
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

    // Buttons must disable while responses remain pending, before optimistic action marks land: AC windows
    // begin at request dispatch, not response arrival.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generating|生成中/ })).toBeDisabled();
    });
    // Failed rows persist and statusMap stays unchanged; previews must not overlay generating placeholders with
    // failures. inFlight presentation overrides failed.
    expect(screen.queryByText(/Generation failed|生成失败/)).not.toBeInTheDocument();
    resolveGen({ task_id: "t2", deduped: false });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generating|生成中/ })).toBeDisabled();
    });
    expect(screen.queryByText(/Generation failed|生成失败/)).not.toBeInTheDocument();
  });

  // Regeneration retains successful rows too, bypassing optimistic statusMap; independently disable by occupancy
  // instead of derived status alone.
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

    // Likewise disable during round trips, not after responses.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generating|生成中/ })).toBeDisabled();
    });
    resolveGen({ task_id: "t2", deduped: false });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generating|生成中/ })).toBeDisabled();
    });
  });

  // Submit-time recheck: captured render signals stale after another entry occupies units in stores;
  // fresh getState() must block and notify, never silently enqueue again.
  it("响应式占用信号尚未追上真实 store 时，生成提交仍被 getState() 新鲜读拦截", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
    const genSpy = vi
      .spyOn(API, "generateReferenceVideoUnit")
      .mockResolvedValue({ task_id: "t1", deduped: false } as never);
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    // Freeze both reactive hooks, simulating captured busy/status lagging later task persistence.
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

    // Anchor starts to exclude fill-videos batches, a different submission path in the next case.
    const generate = await screen.findByRole("button", { name: UNIT_GENERATE_CTA });
    expect(generate).toBeEnabled();

    // After render/before click, Agent enqueue/SSE occupies the unit elsewhere.
    act(() => {
      useTasksStore.setState({ tasks: [runningTask("E1U1")] as never });
    });

    fireEvent.click(generate);
    await waitFor(() => expect(pushToast).toHaveBeenCalled());
    expect(genSpy).not.toHaveBeenCalled();
  });

  // Batch targets all missing-final units. In-flight/needs_replan are server-admission gaps reported individually
  // to reject with zero tasks; browser filtering would expose only healthy subsets and create tasks normally.
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

    // After rendering/before clicking, E1U1 becomes occupied elsewhere.
    act(() => {
      useTasksStore.setState({ tasks: [runningTask("E1U1")] as never });
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
    // No more individual sequential enqueue.
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

  // Upload/generation write the same final file; units may become occupied while pickers stay open,
  // beyond render disabled states, so fresh getState() rechecks at submission.
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

    // After file selection/before confirmation, another entry occupies the unit.
    act(() => {
      useTasksStore.setState({ tasks: [runningTask("E1U1")] as never });
    });

    fireEvent.change(input!, { target: { files: [new File(["x"], "clip.mp4", { type: "video/mp4" })] } });

    await waitFor(() => expect(pushToast).toHaveBeenCalled());
    expect(uploadSpy).not.toHaveBeenCalled();
  });

  // Uploads produce no tasks-store rows, so canvas tracks them. Submit-time checks must include them,
  // or batches enqueue uploading units and race the same final file.
  it("批量生成跳过上传在途的 unit", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
      units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    // Unresolved uploads simulate requests sent but not persisted.
    vi.spyOn(API, "uploadReferenceUnitVideo").mockReturnValue(new Promise(() => {}) as never);
    const batchSpy = vi
      .spyOn(API, "generateReferenceVideoBatch")
      .mockResolvedValue(mkAdmission());
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    const { container } = render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    const batch = await screen.findByRole("button", { name: /Fill in videos|补齐视频/ });
    await waitFor(() => expect(batch).toBeEnabled());

    // Default E1U1 selection makes preview upload target that unit.
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    fireEvent.change(input!, { target: { files: [new File(["x"], "clip.mp4", { type: "video/mp4" })] } });

    fireEvent.click(batch);
    // Uploading units skip with reasons, excluded from preview requests.
    const dialog = within(await screen.findByRole("alertdialog"));
    // Skips render after preview results; await them, not dialog mounting alone.
    expect(await dialog.findByText(/跳过 1 个视频单元|Skipped 1 video unit/)).toBeInTheDocument();
    expect(dialog.getByText(/正在上传视频|Uploading a video/)).toBeInTheDocument();
    expect(API.previewReferenceVideoBatch).toHaveBeenCalledWith("proj", 1, { unit_ids: ["E1U2"] }, expect.anything());
    await confirmBatchPreview();
    await waitFor(() => expect(batchSpy).toHaveBeenCalledTimes(1));
    expect(batchSpy).toHaveBeenCalledWith("proj", 1, {
      unit_ids: ["E1U2"],
    });
  });

  // Empty targets after recheck require feedback; bare returns on both paths would look inert,
  // misleading users into thinking tasks submitted.
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

  // Duration confirmation: discrete models use the smallest tier covering bases between tiers;
  // final videos are not trimmed, so explain differing seconds before enqueue and let users decide.
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

      // Never enqueue before confirmation.
      const confirm = await screen.findByRole("button", { name: CONFIRM_CTA });
      expect(genSpy).not.toHaveBeenCalled();
      // Dialog includes request-duration basis, requested seconds, and longer-final explanation.
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




    // Conversely per-unit regeneration intentionally overwrites finals, so that same recheck must not block it.
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

  // All three batch decisions mean successful evaluation: admitted creates tasks; confirmation_required/blocked
  // create none, requiring full reasons for not starting rather than generic errors. admitted
  // additionally divides clean/interrupted enqueue, the latter naming all unqueued units/reasons.
  describe("整批准入判定", () => {
    const BATCH_CONFIRM_CTA = /Generate at these lengths|按这些档位生成/;

    beforeEach(() => seedFormalUnits(["E1U1", "E1U2", "E1U3"]));

    async function clickBatch() {
      const batch = await screen.findByRole("button", { name: /Fill in videos|补齐视频/ });
      await waitFor(() => expect(batch).toBeEnabled());
      fireEvent.click(batch);
      return batch;
    }

    /** Enter batches/confirm; cases focus on postsubmission outcomes. */
    async function submitBatch() {
      await clickBatch();
      await confirmBatchPreview();
      // Confirmation closes only when submission outcomes arrive; subsequent dialogs present outcomes.
      await waitFor(() => expect(screen.queryByTestId("reference-batch-preview")).not.toBeInTheDocument());
    }

    // Paid video confirms membership/skips/estimates first; cancellation creates no tasks.
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
      // Server-skipped existing-final units list reasons.
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

    // Duration tiers join the same membership/cost confirmation; one decision submits tiers,
    // without second-layer tier dialogs.
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
      // Tier groups show seconds × units plus total costs; incomplete quotes must not invent totals.
      expect(dialog.getByText(/^(?:8 秒|8s)$/)).toBeInTheDocument();
      expect(dialog.getByText(/2 个单元|2 units/)).toBeInTheDocument();
      expect(dialog.getByText(/约 \$1\.60|about \$1\.60/)).toBeInTheDocument();
      expect(dialog.getByText(/报价不可用|price unavailable/)).toBeInTheDocument();
      for (const label of ["视频单元 1", "视频单元 2", "视频单元 3"]) {
        // Membership/tier groups each list once.
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

    // Script/model changes after preview may alter tiers; server submission requests confirmation of
    // a new outcome, resubmitted with new tiers afterward.
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
      // Confirmation hands off to outcomes, one dialog at a time.
      expect(screen.getAllByRole("alertdialog")).toHaveLength(1);
      fireEvent.click(confirm);
      await waitFor(() => expect(batchSpy).toHaveBeenCalledTimes(2));
      expect(batchSpy).toHaveBeenLastCalledWith("proj", 1, {
        unit_ids: ["E1U1", "E1U2"],
        confirmed_request_durations: { E1U2: 8 },
      });
    });

    // Users may deliberate long enough for others to finish units. Frozen-list resubmission bypasses queue
    // dedup limited to queued/running, causing regeneration/double charging/final overwrite.
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

      // While confirmation stays open, Agent/other-tab generation finishes and persists E1U1.
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
            // action uses GenerationAction values normalized server-side across upstream modules;
            // next-step UI chooses keys from that vocabulary.
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
              // Servers return this shape for individually admitted units withheld by other batch gaps:
              // admitted retains its own decision, withheld explains lack of submission.
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
      // All gaps remain, never collapse to the first.
      expect(dialog.getByText("引用的角色图缺失")).toBeInTheDocument();
      expect(dialog.getByText("该单元需要重新规划")).toBeInTheDocument();
      expect(dialog.getByText(/补上或更换缺失的参考素材|add or replace the missing reference/)).toBeInTheDocument();
      expect(dialog.getByText(/改写这个单元|rewrite this unit/)).toBeInTheDocument();
      // Show requested tiers beside gaps so users see differences.
      expect(dialog.getByText(/^(?:申请 12 秒|requesting 12s)$/)).toBeInTheDocument();
      // Separately list collaterally withheld units, explaining their own inputs are sound.
      expect(dialog.getByText(/本身没问题|w(as|ere) fine but w(as|ere) held back/)).toBeInTheDocument();
      for (const label of ["视频单元 1", "视频单元 2", "视频单元 3"]) {
        expect(dialog.getByRole("img", { name: label })).toBeInTheDocument();
      }

      // Blocked batches show no prices/submission.
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
        // Interrupted enqueue never cancels created tasks: decision remains admitted; unreached units enter
        // enqueue_failures individually with localized reasons.
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
      // List each unit/reason, not a single count.
      expect(dialog.getByRole("img", { name: "视频单元 2" })).toBeInTheDocument();
      expect(dialog.getByRole("img", { name: "视频单元 3" })).toBeInTheDocument();
      expect(dialog.getByText("队列暂时不可用")).toBeInTheDocument();
      expect(dialog.getByText("任务创建失败")).toBeInTheDocument();
      // Never mix queued units into unqueued lists.
      expect(dialog.queryByRole("img", { name: "视频单元 1" })).not.toBeInTheDocument();
      // Explain created tasks continue; otherwise partial success is unclear.
      expect(
        dialog.getByText(/再次批量生成就会补上|generate the batch again/),
      ).toBeInTheDocument();
      // Existing-artifact skips differ from enqueue failures and must be explained here too.
      expect(
        dialog.getByText(/已有视频，本次跳过|already had a video and is skipped/),
      ).toBeInTheDocument();
      // Action-layer count notices remain consistent with the same outcomes.
      await waitFor(() => {
        expect(useAppStore.getState().toast?.text).toMatch(/入队中断|Enqueue was interrupted/);
      });

      // Informational outcomes close without resubmission.
      fireEvent.click(dialog.getByRole("button", { name: /Got it|知道了/ }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(batchSpy).toHaveBeenCalledTimes(1);
    });

    it("首个目标就中断时不说成部分排上", async () => {
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
        units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
      vi.spyOn(API, "generateReferenceVideoBatch").mockResolvedValue(
        // Interruption on first target leaves admitted decision but zero created tasks.
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
      // Still list gaps individually.
      expect(dialog.getByRole("img", { name: "视频单元 1" })).toBeInTheDocument();
      expect(dialog.getByRole("img", { name: "视频单元 2" })).toBeInTheDocument();
      // Zero tasks cannot be described as partial success or existing submissions continuing.
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
    // Sole unit already has final video, ready statusMap, leaving no batch targets.
    const ready = mkUnit("E1U1");
    ready.generated_assets.video_clip = "videos/E1U1.mp4";
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [ready], unit_capabilities: {} });

    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);

    const batch = await screen.findByRole("button", { name: /Fill in videos|补齐视频/ });
    await waitFor(() => expect(batch).toBeDisabled());
  });

  // Background failure notices now belong to global useTaskFailureNotifications (transition-driven,
  // no repeated historical/same failure notices tested there); see
  // hooks/useTaskFailureNotifications.test.tsx. Verify return-navigation consumption only here.

  // reference_unit notifications switch/select units without extra history entries.
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
    /** Another dirty editing unit outside the canvas. */
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

  // Slow/cold-start regression: never expire targets while loadUnits remains pending,
  // or arrived units cannot select notification targets and return navigation fails.
  it("keeps a reference_unit target while units are still loading, even past expiry", async () => {
    let resolveList: (v: { units: ReferenceVideoUnit[]; unit_capabilities: ReferenceUnitCapabilityMap }) => void = () => {};
    vi.spyOn(API, "listReferenceVideoUnits").mockReturnValue(
      new Promise((resolve) => {
        resolveList = resolve;
      }),
    );
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    // Issue expired targets while fetch is pending/loading=true; act deterministically flushes
    // location effects rather than fixed delays. Negative waitFor assertions are initially true and cannot prove
    // persistence; setTimeout may assert before effects and miss bugs.
    await act(async () => {
      useAppStore.getState().triggerScrollTo({
        type: "reference_unit",
        id: "E1U2",
        route: "/episodes/1",
        expires_at: Date.now() - 1,
      });
    });
    // Crucial: effects ran, but unfinished loading preserves even expired targets.
    expect(useAppStore.getState().scrollTarget?.id).toBe("E1U2");
    // Arrived units match/select targets, then clear them.
    await act(async () => {
      resolveList({ units: [mkUnit("E1U1"), mkUnit("E1U2")], unit_capabilities: {} });
    });
    await waitFor(() => expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U2"));
    expect(useAppStore.getState().scrollTarget).toBeNull();
  });

  // Fallback regression: loaded units without targets need one-shot expiration cleanup even if
  // dependencies never change again, avoiding permanent store residue.
  it("clears an unresolvable reference_unit target after expiry without further updates", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")], unit_capabilities: {} });
    render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    await waitFor(() => expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U1"));
    // Target absent from list; slightly longer expiry reduces fragility.
    act(() => {
      useAppStore.getState().triggerScrollTo({
        type: "reference_unit",
        id: "E9U9",
        route: "/episodes/1",
        expires_at: Date.now() + 200,
      });
    });
    // First prove target persisted without immediate clearing, selecting timer rather than immediate-cleanup paths.
    expect(useAppStore.getState().scrollTarget?.id).toBe("E9U9");
    // No further dependency changes; only the one-shot timer cleans at expiry.
    await waitFor(() => expect(useAppStore.getState().scrollTarget).toBeNull());
  });

  it("参考生视频分组失效信号自增时重拉分组，展示新落地的成片", async () => {
    // Completed-task terminal state arrives via project SSE → invalidateReferenceVideoUnits →
    // canvas refetches groups, showing finals without manual refresh.
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

    /** Completed clip predating current voice settings; characters must speak for voice to affect it. */
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
          // Banner only in reference-audio binding; prompt-only soft constraints bind no audio and changes do not affect generated voices.
          character_voice_binding: "reference_audio",
          characters: { 王: { description: "", voice_updated_at: VOICE_UPDATED_AT } },
        },
        refreshProject: vi.fn().mockResolvedValue({ status: "ok" }),
      } as never);
      vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [staleUnit()], unit_capabilities: {} });
      render(<ReferenceVideoCanvas {...BOARD} projectName="proj" episode={1} />);
    }

    it("关闭时写回角色自己的 voice_updated_at，而不是本机当前时间", async () => {
      // Client clocks may lag servers; local-now dismissal would permanently fail under drift.
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
      // refreshProject resolves "failed", never rejects; await without checking outcomes/providing
      // onError would make failure completely invisible in UI.
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
});
