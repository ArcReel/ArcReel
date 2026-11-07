import { itemIdsInEpisodeText } from "@/utils/episode-display";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { API, ApiRequestError } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useScriptPlanStore } from "@/stores/script-plan-store";
import { ReferenceScriptPlanPreviewPanel } from "./ReferenceScriptPlanPreviewPanel";
import { makeReferenceUnitCapability, makeScriptOverwrite, makeScriptOverwriteEntry } from "@/test/factories";
import type { MentionLookup } from "@/hooks/useUnitPromptHighlight";
import type {
  EpisodeScript,
  ReferenceScriptPlanDraft,
  ReferenceUnitCapability,
  ScriptReviewQuarantine,
  ScriptReviewState,
  VideoCapabilities,
} from "@/types";

const LOOKUP: MentionLookup = { 阿离: "character", 长街: "scene" };

const VIDEO_CAPS = {
  provider_id: "gemini",
  model: "veo-3",
  supported_durations: [4, 8],
  max_duration: 8,
} as VideoCapabilities;

/** Expand duration options, read selectable seconds, then close. */
async function durationOptions(name = "视频单元 1 时长"): Promise<string[]> {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("combobox", { name }));
  const values = (await screen.findAllByRole("option")).map((option) => option.textContent?.replace(/\D/g, "") ?? "");
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("listbox")).not.toBeInTheDocument());
  return values;
}

async function pickDuration(seconds: number, name = "视频单元 1 时长") {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("combobox", { name }));
  await user.click(await screen.findByRole("option", { name: `${seconds} 秒` }));
}

// Await capability callbacks before asserting no notices; waiting only for spies leaves callbacks pending, making absence vacuously true.
async function settleCapabilityRequests(spy: MockInstance<typeof API.getVideoCapabilities>) {
  await act(async () => {
    await Promise.allSettled(spy.mock.results.map((r) => r.value));
  });
}

// Confirmed episodes normally already have formal scripts, materialized by confirmation.
const CONFIRMED: Partial<ScriptReviewState> = {
  status: "confirmed",
  confirmed_at: "2026-06-26T00:00:00Z",
  script_overwrite: makeScriptOverwrite(),
};

/** Per-unit server buckets; default complete references → r2v, tiers 4/8, overridable per case. */
/** Default: @[阿离]/@[长街] have reference images, r2v tiers 4/8. */
function mkCapability(unitId: string, patch: Partial<ReferenceUnitCapability> = {}): ReferenceUnitCapability {
  return makeReferenceUnitCapability(unitId, {
    declared_capability: "r2v",
    hydrated_capability: "r2v",
    declared_references: [
      { type: "character", name: "阿离" },
      { type: "scene", name: "长街" },
    ],
    allowed_durations: [4, 8],
    ...patch,
  });
}

function pendingState(overrides: Partial<ScriptReviewState> = {}): ScriptReviewState {
  return {
    episode: 1,
    content_mode: "narration",
    status: "pending_review",
    fingerprint: "fp1",
    confirmed_at: null,
    quarantine: null,
    supported_durations: [4, 8],
    duration_tiers: { with_references: [4, 8], units: { E1U01: mkCapability("E1U01") } },
    episode_target_duration: null,
    script_overwrite: null,
    content: {
      units: [
        {
          unit_id: "E1U01",
          text: "@[阿离] 撑伞走过 @[长街]",
          duration_seconds: 8,
          source_text: "阿离撑伞走过长街。",
        },
      ],
    },
    ...overrides,
  };
}

function quarantinedState(): ScriptReviewState {
  return {
    episode: 1,
    content_mode: "narration",
    status: "pending_review",
    fingerprint: null,
    confirmed_at: null,
    supported_durations: [4, 8],
    duration_tiers: null,
    episode_target_duration: null,
    script_overwrite: null,
    content: null,
    quarantine: draftView({
      content: {
        units: [
          {
            duration_seconds: 8,
            source_text: "阿离撑伞走过长街。",
            text: "门开了\n@[阿离]：｛我来了。｝",
          },
        ],
      },
      violations: [
        { code: "fullwidth_braces", label: "unit E1U01", message: "unit E1U01 使用了全角花括号", line: 1, item_index: 0 },
        { code: "dialogue_overload", label: "unit E1U01", message: "unit E1U01 的台词念不完", line: null, item_index: 0 },
      ],
    }),
  };
}

function draftView(overrides: Partial<ScriptReviewQuarantine> = {}): ScriptReviewQuarantine {
  return {
    doc_type: "reference_script_plan",
    revision: "rev-1",
    editable_by: "user",
    content: null,
    violations: [],
    soft_violations: [],
    formal_exists: false,
    ...overrides,
  };
}

describe("ReferenceScriptPlanPreviewPanel", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);
    useScriptPlanStore.setState(useScriptPlanStore.getInitialState(), true);
    // Global confirmation effects (toast/prefill) apply only while users still view this project. Panels render
    // projectName="p", so synchronize current project into stores too.
    useProjectsStore.setState({ currentProjectName: "p" });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    useProjectsStore.setState({ currentScripts: {} });
  });

  // Body `@[名称]` highlights; references resolve at execution only, without separate image lists.
  it("renders the clean pending state with the highlighted body and no reference list", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    await waitFor(() => expect(screen.getByRole("img", { name: "视频单元 1" })).toBeInTheDocument());
    expect(screen.getByText("阿离撑伞走过长街。")).toBeInTheDocument();
    expect(screen.getByText("@[阿离]")).toBeInTheDocument();
    expect(screen.getByText("@[长街]")).toBeInTheDocument();
    expect(screen.queryByText("参考图")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认拆分，继续生成/ })).toBeEnabled();
  });

  it("blocks confirmation when the unit's i2v bucket is unresolved", async () => {
    const state = pendingState({
      duration_tiers: {
        with_references: [8],
        units: {
          E1U01: mkCapability("E1U01", {
            declared_capability: "i2v",
            hydrated_capability: "i2v",
            declared_references: [],
            allowed_durations: null,
            problem: { code: "video_capability_missing_i2v", params: { capability: "i2v" }, action: "configure_video_model" },
          }),
        },
      },
    });
    state.content = { units: [{ unit_id: "E1U01", text: "夜色中行走。", duration_seconds: 8, source_text: "夜色中行走。" }] };
    vi.spyOn(API, "getScriptReview").mockResolvedValue(state);

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    expect(await screen.findByText("图生视频（无参考图）档位未知")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("当前模型不支持图生视频（无参考图）");
    expect(screen.getByRole("alert")).not.toHaveTextContent("video_capability_missing_i2v");
    expect(screen.getByRole("button", { name: /确认拆分，继续生成/ })).toBeDisabled();
    expect(screen.queryByRole("combobox", { name: "视频单元 1 时长" })).not.toBeInTheDocument();
  });

  function endpointFixedState(durationSeconds: number): ScriptReviewState {
    const state = pendingState({
      duration_tiers: {
        with_references: [],
        units: {
          E1U01: mkCapability("E1U01", {
            allowed_durations: [],
            duration_endpoint_fixed: true,
            duration_endpoint_fixed_reason: "endpoint",
          }),
        },
      },
    });
    state.content = {
      units: [{ unit_id: "E1U01", text: "@[阿离] 撑伞走过 @[长街]", duration_seconds: durationSeconds, source_text: "阿离撑伞走过长街。" }],
    };
    return state;
  }

  it("offers the planning tiers for a unit whose bucket is endpoint-fixed", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(endpointFixedState(8));
    render(
      <ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} videoModelUnresolved={false} planningDurations={[4, 8]} />,
    );
    expect(await durationOptions()).toEqual(["4", "8"]);
    expect(screen.getByText(/时长由端点固定/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认拆分，继续生成/ })).toBeEnabled();
  });

  it("flags an endpoint-fixed unit outside the planning tiers and lets the duration be corrected", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(endpointFixedState(6));
    render(
      <ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} videoModelUnresolved={false} planningDurations={[4, 8]} />,
    );
    expect(await screen.findByText("档位已失效")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认拆分，继续生成/ })).toBeDisabled();

    await pickDuration(8);
    expect(screen.queryByText("档位已失效")).not.toBeInTheDocument();
  });

  it("keeps duration read-only for a unit the server has not judged yet", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      pendingState({ duration_tiers: { with_references: [4, 8], units: {} } }),
    );
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    expect(await screen.findByRole("img", { name: "视频单元 1" })).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "视频单元 1 时长" })).not.toBeInTheDocument();
  });

  it("localizes structured speech violations with their unit and field locations", async () => {
    const state = quarantinedState();
    state.quarantine!.violations = [{
      code: "mixed_speech",
      label: "unit E1U01",
      message: "raw agent message",
      line: null,
      locations: [
        { path: ["text"], line: 1 },
        { path: ["text"], line: 2 },
      ],
      reason: "character_and_narrator_mixed",
      action: "replan_unit",
      item_index: 0,
    }];
    vi.spyOn(API, "getScriptReview").mockResolvedValue(state);

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    expect(await screen.findByText(/^视频单元 1 在 .*同时包含角色台词和旁白/)).toHaveTextContent("text:2");
    expect(screen.queryByText("raw agent message")).not.toBeInTheDocument();
  });

  // Missing-scene hints match parse previews, attach within unit cards, and never block confirmation.
  it("warns on units that reference no scene while the project registers scene assets", async () => {
    const state = pendingState();
    state.content = { units: [{ unit_id: "E1U01", text: "@[阿离] 推门。", duration_seconds: 8, source_text: "阿离推门。" }] };
    vi.spyOn(API, "getScriptReview").mockResolvedValue(state);

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    expect(await screen.findByText("本单元未引用场景，画面地点将由模型自由决定")).toBeInTheDocument();
    // Fallback hints do not gate confirmation: legal body, unconstrained scene location.
    expect(screen.getByRole("button", { name: /确认拆分，继续生成/ })).toBeEnabled();
  });

  it("drops the scene warning once the edited body references a scene", async () => {
    const state = pendingState();
    state.content = { units: [{ unit_id: "E1U01", text: "@[阿离] 推门。", duration_seconds: 8, source_text: "阿离推门。" }] };
    vi.spyOn(API, "getScriptReview").mockResolvedValue(state);

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    fireEvent.click(await screen.findByRole("button", { name: "编辑文稿" }));
    fireEvent.change(screen.getByRole("textbox", { name: "视频单元 1 正文" }), {
      target: { value: "@[阿离] 推门走进 @[长街]。" },
    });

    await waitFor(() =>
      expect(screen.queryByText("本单元未引用场景，画面地点将由模型自由决定")).not.toBeInTheDocument(),
    );
  });

  it.each([
    ["register", false],
    ["skip", true],
  ] as const)("judges the scene reference with a new scene decided as %s", async (decision, warns) => {
    const state = pendingState();
    state.content = {
      units: [{ unit_id: "E1U01", text: "@[阿离] 推门走进 @[酒馆]。", duration_seconds: 8, source_text: "阿离推门。" }],
      new_assets: [
        { type: "scene", name: "酒馆", decision, reason: "", description: "", aliases: [], target: "", asset_name: "" },
      ],
    };
    vi.spyOn(API, "getScriptReview").mockResolvedValue(state);

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    await waitFor(() => expect(screen.getByText("本集新增资产")).toBeInTheDocument());
    expect(screen.queryByText("本单元未引用场景，画面地点将由模型自由决定") !== null).toBe(warns);
  });

  it("stays silent about scenes when the project registers none", async () => {
    const state = pendingState();
    state.content = { units: [{ unit_id: "E1U01", text: "@[阿离] 推门。", duration_seconds: 8, source_text: "阿离推门。" }] };
    vi.spyOn(API, "getScriptReview").mockResolvedValue(state);

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={{ 阿离: "character" }} />);

    await waitFor(() => expect(screen.getByRole("img", { name: "视频单元 1" })).toBeInTheDocument());
    expect(screen.queryByText("本单元未引用场景，画面地点将由模型自由决定")).not.toBeInTheDocument();
  });

  it("confirms without opening the assistant panel or prefilling a message", async () => {
    // Web confirmation never opens Agents; page next steps continue, matching ScriptReviewGate.
    useAppStore.setState({ assistantPanelOpen: false });
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    const confirm = vi
      .spyOn(API, "confirmScriptReview")
      .mockResolvedValue(pendingState(CONFIRMED));

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    await waitFor(() => expect(screen.getByRole("button", { name: /确认拆分，继续生成/ })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /确认拆分，继续生成/ }));

    await waitFor(() => expect(confirm).toHaveBeenCalledWith("p", 1, {}));
    await waitFor(() => expect(screen.getByRole("button", { name: "已确认" })).toBeDisabled());
    expect(useAppStore.getState().toast?.text).toBe("已确认 — 视觉生成已放行");
    expect(useAssistantStore.getState().input).toBe("");
    expect(useAppStore.getState().assistantPanelOpen).toBe(false);
  });

  it("renders confirmed units without edit controls and offers the timeline", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState(CONFIRMED));
    const openTimeline = vi.fn();

    render(
      <ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} onOpenTimeline={openTimeline} />,
    );

    expect(await screen.findByRole("img", { name: "视频单元 1" })).toBeInTheDocument();
    expect(screen.getByText("8 秒")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "视频单元 1 时长" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑文稿" })).not.toBeInTheDocument();
    expect(screen.getByText("内容已确认，此处只读。请在时间线上修改；要整集重做，请重跑脚本规划后再确认。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "去时间线修改" }));
    expect(openTimeline).toHaveBeenCalledTimes(1);
  });

  it("lets a confirmed episode without a formal script confirm again to build it", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState({ ...CONFIRMED, script_overwrite: null }));
    const confirm = vi.spyOn(API, "confirmScriptReview").mockResolvedValue(pendingState(CONFIRMED));

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    const button = await screen.findByRole("button", { name: "重新确认并生成正式脚本" });
    expect(button).toBeEnabled();
    expect(screen.getByText("内容已确认，但本集还没有正式脚本。重新确认即按脚本规划转出正式脚本。")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "视频单元 1 时长" })).not.toBeInTheDocument();

    fireEvent.click(button);

    await waitFor(() => expect(confirm).toHaveBeenCalledWith("p", 1, {}));
    expect(await screen.findByRole("button", { name: "已确认" })).toBeDisabled();
  });

  it("confirms over an existing formal script only through the danger overwrite dialog", async () => {
    const overwrite = makeScriptOverwrite({
      revision: "sha256-v1:listed",
      entries: [makeScriptOverwriteEntry("E1U01", { has_video: true })],
      video_count: 1,
      text: "现有 1 条分镜全部移除。视频 1 段。",
    });
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState({ script_overwrite: overwrite }));
    const confirm = vi
      .spyOn(API, "confirmScriptReview")
      .mockResolvedValue(pendingState(CONFIRMED));

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    const button = await screen.findByRole("button", { name: "确认并覆盖正式脚本" });
    expect(screen.queryByRole("button", { name: /确认拆分，继续生成/ })).not.toBeInTheDocument();

    fireEvent.click(button);
    expect(await screen.findByRole("alertdialog")).toHaveTextContent(itemIdsInEpisodeText(overwrite.text));
    fireEvent.click(screen.getByRole("button", { name: "覆盖并确认" }));

    await waitFor(() => expect(confirm).toHaveBeenCalledWith("p", 1, { overwriteRevision: "sha256-v1:listed" }));
  });

  it("keeps the overwrite dialog open with the refreshed list when the formal script changed meanwhile", async () => {
    const listed = makeScriptOverwrite({
      revision: "sha256-v1:listed",
      entries: [makeScriptOverwriteEntry("E1U01", { has_video: true })],
      video_count: 1,
      text: "现有 1 条分镜全部移除。视频 1 段。",
    });
    const refreshed = makeScriptOverwrite({
      revision: "sha256-v1:refreshed",
      entries: [
        makeScriptOverwriteEntry("E1U01", { has_video: true }),
        makeScriptOverwriteEntry("E1U05", { has_video: true }),
      ],
      video_count: 2,
      text: "现有 2 条分镜全部移除。视频 2 段。E1U05（视频）",
    });
    // Formal E1U05 is third.
    useProjectsStore.setState({
      currentScripts: {
        "episode_1.json": {
          episode: 1,
          video_units: [{ unit_id: "E1U01" }, { unit_id: "E1U03" }, { unit_id: "E1U05" }],
        } as unknown as EpisodeScript,
      },
    });
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState({ script_overwrite: listed }));
    const confirm = vi
      .spyOn(API, "confirmScriptReview")
      .mockRejectedValueOnce(new ApiRequestError("正式脚本已变化", { script_overwrite: refreshed }, 409))
      .mockResolvedValueOnce(pendingState(CONFIRMED));

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    fireEvent.click(await screen.findByRole("button", { name: "确认并覆盖正式脚本" }));
    fireEvent.click(await screen.findByRole("button", { name: "覆盖并确认" }));

    // Loss lists address formal-script entries by their actual positions.
    await waitFor(() =>
      expect(screen.getByRole("alertdialog")).toHaveTextContent("现有 2 条分镜全部移除。视频 2 段。视频单元 3（视频）"),
    );
    expect(screen.getByRole("alertdialog")).not.toHaveTextContent("U05");

    fireEvent.click(screen.getByRole("button", { name: "覆盖并确认" }));
    await waitFor(() => expect(confirm).toHaveBeenLastCalledWith("p", 1, { overwriteRevision: "sha256-v1:refreshed" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it.each([400, 422])("warns and disables confirm when capabilities return %i", async (status) => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    vi.spyOn(API, "getVideoCapabilities").mockRejectedValue(new ApiRequestError("无法解析", undefined, status));

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    const notice = await screen.findByRole("alert");
    expect(notice).toHaveTextContent("尚未配置可用的视频模型");
    expect(screen.getByRole("link", { name: "前往项目设置" })).toHaveAttribute("href", "/app/projects/p/settings?tab=models");
    const button = screen.getByRole("button", { name: /确认拆分，继续生成/ });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", expect.stringContaining("尚未配置可用的视频模型"));
  });

  it("disables the overwrite confirm too when the video model cannot be resolved", async () => {
    const overwrite = makeScriptOverwrite({ revision: "sha256-v1:listed" });
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState({ script_overwrite: overwrite }));
    vi.spyOn(API, "getVideoCapabilities").mockRejectedValue(new ApiRequestError("无法解析", undefined, 422));

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "确认并覆盖正式脚本" })).toBeDisabled();
  });

  it("disables the in-dialog confirm when the video model turns out unresolvable after the dialog opened", async () => {
    const overwrite = makeScriptOverwrite({ revision: "sha256-v1:listed" });
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState({ script_overwrite: overwrite }));
    let rejectCapabilities: (reason: unknown) => void = () => {};
    vi.spyOn(API, "getVideoCapabilities").mockReturnValue(
      new Promise<VideoCapabilities>((_resolve, reject) => {
        rejectCapabilities = reject;
      }),
    );

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    fireEvent.click(await screen.findByRole("button", { name: "确认并覆盖正式脚本" }));
    expect(await screen.findByRole("button", { name: "覆盖并确认" })).toBeEnabled();

    await act(async () => {
      rejectCapabilities(new ApiRequestError("无法解析", undefined, 422));
    });

    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "覆盖并确认" })).toBeDisabled();
  });

  it("shows no video model warning once capabilities resolve", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    const capabilities = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(VIDEO_CAPS);

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    const button = await screen.findByRole("button", { name: /确认拆分，继续生成/ });
    await waitFor(() => expect(capabilities).toHaveBeenCalled());
    await settleCapabilityRequests(capabilities);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(button).toBeEnabled();
  });

  it("keeps confirm available when the capability request itself fails", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    const capabilities = vi.spyOn(API, "getVideoCapabilities").mockRejectedValue(new TypeError("Failed to fetch"));

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    const button = await screen.findByRole("button", { name: /确认拆分，继续生成/ });
    await waitFor(() => expect(capabilities).toHaveBeenCalled());
    await settleCapabilityRequests(capabilities);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(button).toBeEnabled();
  });

  it("suppresses the confirm toast if the user has switched to a different project mid-request", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    let resolveConfirm: (value: ScriptReviewState) => void = () => {};
    vi.spyOn(API, "confirmScriptReview").mockReturnValue(
      new Promise((resolve) => {
        resolveConfirm = resolve;
      }),
    );

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    fireEvent.click(await screen.findByRole("button", { name: /确认拆分，继续生成/ }));

    // During confirmation users change projects, potentially unmounting tabs; even if still mounted,
    // no stale global side effects may occur.
    useProjectsStore.setState({ currentProjectName: "other-project" });
    resolveConfirm(pendingState(CONFIRMED));

    // adopt() precedes guards and confirmation already applied; changed buttons prove asynchronous completion,
    // avoiding accidentally passing assertions empty throughout.
    await waitFor(() => expect(screen.getByRole("button", { name: "已确认" })).toBeDisabled());
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("still shows the confirm toast after switching tabs within the same project", async () => {
    // Same-project tab switches unmount panels but retain useProjectsStore.currentProjectName; never
    // suppress confirmation notices as though project navigation.
    useAppStore.setState({ assistantPanelOpen: false });
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    let resolveConfirm: (value: ScriptReviewState) => void = () => {};
    vi.spyOn(API, "confirmScriptReview").mockReturnValue(
      new Promise((resolve) => {
        resolveConfirm = resolve;
      }),
    );

    const { unmount } = render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    fireEvent.click(await screen.findByRole("button", { name: /确认拆分，继续生成/ }));

    unmount();
    resolveConfirm(pendingState(CONFIRMED));

    await waitFor(() => expect(useAppStore.getState().toast?.text).toBe("已确认 — 视觉生成已放行"));
    expect(useAssistantStore.getState().input).toBe("");
    expect(useAppStore.getState().assistantPanelOpen).toBe(false);
  });

  it("draft state anchors a line-level violation inline and aggregates the unit-level one, without a confirm", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(quarantinedState());
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    await waitFor(() => expect(screen.getByRole("img", { name: "视频单元 1" })).toBeInTheDocument());
    expect(screen.getByText("视频单元 1 使用了全角花括号")).toBeInTheDocument();
    expect(screen.getByText("视频单元 1 的台词念不完")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /确认拆分，继续生成/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "视频单元 1 · 2" })).toBeInTheDocument();
  });

  it("hand-fixes the draft's unit body and duration, then saves it for validation", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(quarantinedState());
    const save = vi
      .spyOn(API, "saveEpisodeDraft")
      .mockResolvedValue({ episode: 1, doc_type: "reference_script_plan", adopted: true, draft: null });
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    fireEvent.click(await screen.findByRole("button", { name: "编辑文稿" }));
    fireEvent.change(screen.getByRole("textbox", { name: "视频单元 1 正文" }), {
      target: { value: "门开了\n@[阿离]：我来了。" },
    });
    await pickDuration(4);
    fireEvent.click(screen.getByRole("button", { name: /保存并校验/ }));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith(
        "p",
        1,
        "reference_script_plan",
        { units: [{ duration_seconds: 4, source_text: "阿离撑伞走过长街。", text: "门开了\n@[阿离]：我来了。" }] },
        "rev-1",
      ),
    );
  });

  function anchorBrokenState(): ScriptReviewState {
    return {
      ...quarantinedState(),
      quarantine: draftView({
        content: { units: [{ duration_seconds: 8, source_text: "阿离撑伞走过。", text: "@[阿离] 撑伞走过 @[长街]" }] },
        violations: [
          {
            code: "source_text_not_verbatim",
            label: "unit E1U01",
            message: "unit E1U01 的 source_text 不是小说原文的逐字片段（存在改写、翻译或杜撰）",
            line: null,
            item_index: 0,
          },
        ],
      }),
    };
  }

  it("hand-fixes a broken source anchor in the draft, then unblocks confirmation once adopted", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValueOnce(anchorBrokenState()).mockResolvedValue(pendingState());
    const save = vi
      .spyOn(API, "saveEpisodeDraft")
      .mockResolvedValue({ episode: 1, doc_type: "reference_script_plan", adopted: true, draft: null });
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    expect(await screen.findByText("非源文逐字子串")).toBeInTheDocument();
    expect(screen.getByText(/视频单元 1 的 source_text 不是小说原文的逐字片段/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /确认拆分，继续生成/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "编辑文稿" }));
    const source = screen.getByRole("textbox", { name: "视频单元 1 原文" });
    expect(source).toHaveAttribute("aria-invalid", "true");
    fireEvent.change(source, { target: { value: "阿离撑伞走过长街。" } });
    fireEvent.click(screen.getByRole("button", { name: /保存并校验/ }));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith(
        "p",
        1,
        "reference_script_plan",
        { units: [{ duration_seconds: 8, source_text: "阿离撑伞走过长街。", text: "@[阿离] 撑伞走过 @[长街]" }] },
        "rev-1",
      ),
    );
    expect(await screen.findByRole("button", { name: /确认拆分，继续生成/ })).toBeEnabled();
    expect(screen.queryByText("非源文逐字子串")).not.toBeInTheDocument();
  });

  it("keeps blocking on the server's verbatim check when the edited source text is still not in the novel", async () => {
    const stillBroken = anchorBrokenState();
    vi.spyOn(API, "getScriptReview").mockResolvedValue(stillBroken);
    const save = vi.spyOn(API, "saveEpisodeDraft").mockResolvedValue({
      episode: 1,
      doc_type: "reference_script_plan",
      adopted: false,
      draft: { ...stillBroken.quarantine!, revision: "rev-2", episode: 1, item_ids: ["E1U01"] },
    });
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    fireEvent.click(await screen.findByRole("button", { name: "编辑文稿" }));
    fireEvent.change(screen.getByRole("textbox", { name: "视频单元 1 原文" }), { target: { value: "杜撰的一句话。" } });
    fireEvent.click(screen.getByRole("button", { name: /保存并校验/ }));

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][3]).toMatchObject({ units: [{ source_text: "杜撰的一句话。" }] });
    expect(await screen.findByText(/视频单元 1 的 source_text 不是小说原文的逐字片段/)).toBeInTheDocument();
    expect(screen.getByText("非源文逐字子串")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /确认拆分，继续生成/ })).not.toBeInTheDocument();
  });

  it("shows server soft violations on their unit without blocking a save", async () => {
    const state = quarantinedState();
    state.quarantine = draftView({
      content: state.quarantine!.content,
      soft_violations: [
        { code: "ref_warn_speaker_without_audio", params: {}, item_index: 0, item_id: "E1U01", message: "阿离未设置参考音频" },
      ],
    });
    vi.spyOn(API, "getScriptReview").mockResolvedValue(state);
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    expect(await screen.findByText("阿离未设置参考音频")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /保存并校验/ })).toBeEnabled();
  });

  it("prefills a structured fix-request report on 'ask the assistant to fix it', without sending", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(quarantinedState());
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    fireEvent.click(await screen.findByRole("button", { name: /交给 Agent 修复/ }));

    const input = useAssistantStore.getState().input;
    expect(input).toContain("（集 ID 1）");
    expect(input).toContain("doc_type=reference_script_plan");
    expect(input).toContain("open_draft 返回的 revision 作为 base_revision");
    expect(input).toContain("1. unit E1U01 使用了全角花括号");
    expect(input).toContain("2. unit E1U01 的台词念不完");
    expect(useAppStore.getState().assistantPanelOpen).toBe(true);
  });

  const NO_PLAN: ScriptReviewState = {
    episode: 1,
    content_mode: "narration",
    status: "no_script_plan",
    fingerprint: null,
    confirmed_at: null,
    content: null,
    quarantine: null,
    supported_durations: null,
    duration_tiers: null,
    episode_target_duration: null,
    script_overwrite: null,
  };

  it("shows the first-plan starter when the episode has neither a plan nor a formal script", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(NO_PLAN);
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    expect(await screen.findByRole("heading", { name: "这一集还没有脚本" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "附加指令（可选）" })).toBeInTheDocument();
    expect(screen.queryByText("暂无脚本规划结果")).not.toBeInTheDocument();
  });

  it("offers a re-plan that replaces the formal script when only the formal script exists", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue({ ...NO_PLAN, script_overwrite: makeScriptOverwrite() });
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    expect(await screen.findByText("暂无脚本规划结果")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "AI 规划脚本" }));
    expect(useScriptPlanStore.getState().request).toEqual({ projectName: "p", episode: 1, replaces: "formal_script" });
    expect(screen.queryByRole("heading", { name: "这一集还没有脚本" })).not.toBeInTheDocument();
  });

  it("edits the unit body in the non-quarantined state and persists the units draft", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(pendingState());

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    await waitFor(() => expect(screen.getByRole("img", { name: "视频单元 1" })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "编辑文稿" }));
    const textarea = await screen.findByDisplayValue("@[阿离] 撑伞走过 @[长街]");
    fireEvent.change(textarea, { target: { value: "@[阿离] 缓步走过 @[长街]" } });

    const saveBtn = await screen.findByText("保存");
    fireEvent.click(saveBtn);

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    const [, , savedContent, baseFingerprint] = save.mock.calls[0];
    expect(savedContent).toMatchObject({
      units: [{ unit_id: "E1U01", text: "@[阿离] 缓步走过 @[长街]" }],
    });
    // Save sends GET content fingerprints for server concurrent-edit checks.
    expect(baseFingerprint).toBe("fp1");
  });

  it("edits the unit source text in the non-quarantined state and saves it with the units", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(pendingState());
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    fireEvent.click(await screen.findByRole("button", { name: "编辑文稿" }));
    fireEvent.change(screen.getByRole("textbox", { name: "视频单元 1 原文" }), { target: { value: "阿离撑伞。" } });
    fireEvent.click(await screen.findByText("保存"));

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][2]).toMatchObject({
      units: [{ unit_id: "E1U01", source_text: "阿离撑伞。", text: "@[阿离] 撑伞走过 @[长街]" }],
    });
  });

  it("renders the spoken-line count in the unit header", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(quarantinedState());
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    // Draft has one description line and one full-width-brace dialogue line. Counts share highlighting segmentation; full-width braces
    // are not parsed as dialogue (the fullwidth_braces violation), so count zero.
    await waitFor(() => expect(screen.getByText("0 句台词")).toBeInTheDocument());
  });

  it("counts a well-formed dialogue line as a spoken line", async () => {
    const withDialogue = pendingState();
    (withDialogue.content as ReferenceScriptPlanDraft).units[0].text =
      "@[阿离] 撑伞走过 @[长街]\n@[阿离]：{我来了。}";
    vi.spyOn(API, "getScriptReview").mockResolvedValue(withDialogue);
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    await waitFor(() => expect(screen.getByText("1 句台词")).toBeInTheDocument());
  });

  it("picks a duration from the supported tiers and saves it on the unit", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(pendingState());

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    await pickDuration(4);

    fireEvent.click(await screen.findByText("保存"));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][2]).toMatchObject({ units: [{ duration_seconds: 4 }] });
  });

  it("falls back to a read-only duration when no tier list is available", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState({ supported_durations: null, duration_tiers: null }));
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    await waitFor(() => expect(screen.getByText("8 秒")).toBeInTheDocument());
    expect(screen.queryByRole("combobox", { name: "视频单元 1 时长" })).not.toBeInTheDocument();
  });

  it("keeps the duration select on a stored value that is no longer a supported tier, sorted into place", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState({
      supported_durations: [4, 6],
      duration_tiers: {
        with_references: [4, 6],
        units: { E1U01: mkCapability("E1U01", { allowed_durations: [4, 6] }) },
      },
    }));
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    expect(await screen.findByRole("combobox", { name: "视频单元 1 时长" })).toHaveTextContent("8 秒");
    // Insert saved out-of-tier fallback options numerically, not at front, which would disorder 8/4/6
    // display.
    expect(await durationOptions()).toEqual(["4", "6", "8"]);
  });

  it("pins unit-less violations at the top and offers no hand fix when the draft structure is broken", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue({
      ...quarantinedState(),
      quarantine: draftView({
        // Schema violation: backend returns Agent-edited contents unchanged, with non-array `units`.
        content: { units: "被改坏了" },
        violations: [{ code: "schema_invalid", label: "", message: "待修复草稿的 content.units 必须是非空数组", line: null }],
      }),
    });
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    await waitFor(() => expect(screen.getByText("待修复草稿的 content.units 必须是非空数组")).toBeInTheDocument());
    expect(screen.getByText("整集层面的问题")).toBeInTheDocument();
    expect(screen.queryByText(/被改坏了/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /保存并校验/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /交给 Agent 修复/ })).toBeInTheDocument();
  });

  it("disables the duration select and body textarea while a save is in flight", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    let resolveSave: (value: ScriptReviewState) => void = () => {};
    vi.spyOn(API, "saveScriptReviewContent").mockReturnValue(
      new Promise((resolve) => {
        resolveSave = resolve;
      }),
    );

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    fireEvent.click(await screen.findByRole("button", { name: "编辑文稿" }));
    const textarea = await screen.findByDisplayValue("@[阿离] 撑伞走过 @[长街]");
    fireEvent.change(textarea, { target: { value: "@[阿离] 缓步走过 @[长街]" } });
    const select = await screen.findByRole<HTMLSelectElement>("combobox", { name: "视频单元 1 时长" });

    fireEvent.click(await screen.findByText("保存"));

    await waitFor(() => expect(textarea).toBeDisabled());
    expect(select).toBeDisabled();

    resolveSave(pendingState());
    await waitFor(() => expect(textarea).toBeEnabled());
  });

  it("uses the unit's server-narrowed tiers when its references all have images", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      pendingState({
        supported_durations: [4, 6, 8],
        duration_tiers: {
          with_references: [8],
          units: { E1U01: mkCapability("E1U01", { allowed_durations: [8] }) },
        },
      }),
    );
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    // Servers find @[阿离]/@[长街] images, r2v narrowed to 8s, removing 4/6 choices.
    expect(await durationOptions()).toEqual(["8"]);
    expect(screen.queryByText(/引用的资产缺图/)).not.toBeInTheDocument();
  });

  // Registered-but-imageless names resolve i2v server-side; use i2v tiers/name missing refs, never infer r2v from mentions.
  it("follows the server i2v bucket for a registered-but-imageless mention", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      pendingState({
        supported_durations: [4, 6, 8],
        duration_tiers: {
          with_references: [8],
          units: {
            E1U01: mkCapability("E1U01", {
              hydrated_capability: "i2v",
              unavailable_references: [{ type: "character", name: "阿离" }],
              allowed_durations: [4, 6, 8],
              problems: [
                { code: "reference_asset_missing", blocking: true, unit_id: "E1U01", locations: [], params: {}, action: "repair_reference_assets" },
                { code: "reference_capability_changed", blocking: true, unit_id: "E1U01", locations: [], params: {}, action: "repair_reference_assets" },
              ],
            }),
          },
        },
      }),
    );
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    expect(await durationOptions()).toEqual(["4", "6", "8"]);
    // Structured divergence shares canvas semantics: name missing images/explain actual i2v rather than declared r2v, without blocking confirmation.
    const alert = screen.getByTestId("reference-split-alert");
    expect(alert).toHaveTextContent("引用的资产缺图：阿离");
    expect(alert).toHaveTextContent("本单元将按图生视频（无参考图）执行，而非声明的参考生视频（带参考图）");
    expect(screen.getByRole("button", { name: /确认/ })).toBeEnabled();
  });

  it("names an unregistered mention in the split alert without claiming a bucket change", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      pendingState({
        supported_durations: [4, 6, 8],
        duration_tiers: {
          with_references: [8],
          units: {
            E1U01: mkCapability("E1U01", {
              declared_capability: "i2v",
              hydrated_capability: "i2v",
              declared_references: [],
              unregistered_references: ["路人"],
              allowed_durations: [4, 6, 8],
              problems: [
                { code: "reference_asset_unregistered", blocking: true, unit_id: "E1U01", locations: [], params: {}, action: "repair_reference_assets" },
              ],
            }),
          },
        },
      }),
    );
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    const alert = await screen.findByTestId("reference-split-alert");
    expect(alert).toHaveTextContent("未登记的引用：路人");
    expect(alert).not.toHaveTextContent("本单元将按");
  });

  it("blocks confirmation and flags a unit whose stored duration has fallen out of the effective tier", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      pendingState({
        supported_durations: [4, 6, 8],
        // Effective tiers narrow to 4/6; retain invalid saved 8 for display without silent changes.
        duration_tiers: {
          with_references: [4, 6],
          units: { E1U01: mkCapability("E1U01", { allowed_durations: [4, 6] }) },
        },
      }),
    );
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    expect(await screen.findByRole("combobox", { name: "视频单元 1 时长" })).toHaveTextContent("8 秒");
    expect(screen.getByText("档位已失效")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认拆分，继续生成/ })).toBeDisabled();
  });

  it("takes the tier choice from the saved server result rather than the live-edited body", async () => {
    const withoutReferences = pendingState({
      supported_durations: [4, 6, 8],
      // Both tier sets contain 8, avoiding initial out-of-tier fallback insertion; second assertions isolate
      // tier switching without overlapping fallback display behavior.
      duration_tiers: {
        with_references: [8],
        units: {
          E1U01: mkCapability("E1U01", {
            declared_capability: "i2v",
            hydrated_capability: "i2v",
            declared_references: [],
            allowed_durations: [4, 6, 8],
          }),
        },
      },
    });
    (withoutReferences.content as ReferenceScriptPlanDraft).units[0].text = "门开了";
    (withoutReferences.content as ReferenceScriptPlanDraft).units[0].duration_seconds = 8;
    vi.spyOn(API, "getScriptReview").mockResolvedValue(withoutReferences);
    const saved = pendingState({
      supported_durations: [4, 6, 8],
      duration_tiers: {
        with_references: [8],
        units: { E1U01: mkCapability("E1U01", { allowed_durations: [8] }) },
      },
    });
    (saved.content as ReferenceScriptPlanDraft).units[0].text = "@[阿离] 推门而入。";
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(saved);
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    // Initially no refs: server i2v, all 4/6/8 selectable.
    expect(await durationOptions()).toEqual(["4", "6", "8"]);

    // Unsaved @[阿离] edits do not infer registration; retain prior server tier decisions.
    fireEvent.click(screen.getByRole("button", { name: "编辑文稿" }));
    const textarea = await screen.findByDisplayValue("门开了");
    fireEvent.change(textarea, { target: { value: "@[阿离] 推门而入。" } });
    expect(await durationOptions()).toEqual(["4", "6", "8"]);

    // After save, server rebuckets usable references to r2v, only 8s.
    fireEvent.click(await screen.findByText("保存"));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    await act(async () => {
      await save.mock.results[0].value;
    });
    expect(await durationOptions()).toEqual(["8"]);
  });

  it("lets a draft without violations be saved to adopt it, or handed to the assistant to promote", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue({
      ...quarantinedState(),
      quarantine: draftView({ content: quarantinedState().quarantine!.content }),
    });
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    // Cleared violations allow saving unchanged content, adopting immediately.
    expect(await screen.findByRole("button", { name: /保存并校验/ })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: /交给 Agent 修复/ }));
    const input = useAssistantStore.getState().input;
    expect(input).toContain("promote_draft");
    expect(input).toContain("doc_type=reference_script_plan");
    expect(input).not.toContain("违约待修复");
  });

  it("shows only a status for the agent's editable draft and keeps the formal units read-only", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      pendingState({ quarantine: draftView({ editable_by: "agent", formal_exists: true }) }),
    );
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    expect(await screen.findByText("Agent 有一份未完成的修改")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "视频单元 1" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑文稿" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /确认拆分，继续生成/ })).not.toBeInTheDocument();
    act(() => useAppStore.getState().setAssistantPanelOpen(false));
    fireEvent.click(screen.getByRole("button", { name: /交给 Agent 完成/ }));
    expect(useAssistantStore.getState().input).toContain("doc_type=reference_script_plan");
    expect(useAppStore.getState().assistantPanelOpen).toBe(true);
  });

  it("separates multiple violating-unit locator links in the status bar", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue({
      ...quarantinedState(),
      quarantine: draftView({
        content: {
          units: [
            { duration_seconds: 8, source_text: "阿离撑伞走过长街。", text: "门开了\n@[阿离]：｛我来了。｝" },
            { duration_seconds: 4, source_text: "长街空无一人。", text: "门开了\n@[长街]：｛静悄悄。｝" },
          ],
        },
        violations: [
          { code: "fullwidth_braces", label: "unit E1U01", message: "unit E1U01 使用了全角花括号", line: 1, item_index: 0 },
          { code: "fullwidth_braces", label: "unit E1U02", message: "unit E1U02 使用了全角花括号", line: 1, item_index: 1 },
        ],
      }),
    });
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    const firstLink = await screen.findByRole("button", { name: "视频单元 1 · 1" });

    // Visible text between buttons needs separators, not "视频单元 1 · 1视频单元 2 · 1". Each button's independent accessible name
    // is unaffected; only raw rendered text joins, so directly assert
    // status-bar textContent.
    const statusBar = firstLink.parentElement?.parentElement;
    expect(statusBar?.textContent).toMatch(/视频单元 1 · 1.+视频单元 2 · 1/);
    expect(statusBar?.textContent).not.toContain("1视频单元 2");
  });

  it("compares the episode total against the project target", async () => {
    // pendingState has one 8s unit.
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState({ episode_target_duration: 30 }));
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    await waitFor(() => expect(screen.getByText("本集合计 8 秒 / 目标 30 秒")).toBeInTheDocument());
  });

  it("shows no comparison when the project has no target", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    await waitFor(() => expect(screen.getByRole("img", { name: "视频单元 1" })).toBeInTheDocument());
    expect(screen.queryByText(/本集合计/)).not.toBeInTheDocument();
  });
});
