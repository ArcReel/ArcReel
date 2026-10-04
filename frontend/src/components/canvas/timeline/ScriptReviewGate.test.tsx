import { itemIdsInEpisodeText } from "@/utils/episode-display";
import { describe, it, expect, vi, afterEach, type MockInstance } from "vitest";
import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { ScriptReviewGate } from "./ScriptReviewGate";
import { API, ApiRequestError } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useScriptPlanStore } from "@/stores/script-plan-store";
import { makeScriptOverwrite, makeScriptOverwriteEntry, makeTask } from "@/test/factories";
import type {
  NarrationScriptPlanDraft,
  PlanNewAsset,
  ProjectData,
  ScriptReviewQuarantine,
  ScriptReviewState,
  TaskItem,
  VideoCapabilities,
} from "@/types";

const VIDEO_CAPS = {
  provider_id: "gemini",
  model: "veo-3",
  supported_durations: [4, 8],
  max_duration: 8,
} as VideoCapabilities;

// 等能力请求的回调落地后再断言「无提示」：只等到 spy 被调用时回调尚未执行，「无提示」恒成立。
async function settleCapabilityRequests(spy: MockInstance<typeof API.getVideoCapabilities>) {
  await act(async () => {
    await Promise.allSettled(spy.mock.results.map((r) => r.value));
  });
}

// 已确认的集照常已有正式脚本（确认即转出）。
const CONFIRMED: Partial<ScriptReviewState> = {
  status: "confirmed",
  confirmed_at: "2026-06-26T00:00:00Z",
  script_overwrite: makeScriptOverwrite(),
};

function dramaState(overrides: Partial<ScriptReviewState> = {}): ScriptReviewState {
  return {
    episode: 1,
    content_mode: "drama",
    status: "pending_review",
    fingerprint: "fp1",
    confirmed_at: null,
    quarantine: null,
    supported_durations: null,
    duration_tiers: null,
    episode_target_duration: null,
    script_overwrite: null,
    content: {
      title: "第一集",
      scenes: [
        {
          scene_id: "E1S01",
          duration_seconds: 8,
          segment_break: false,
          characters_in_scene: ["阿离"],
          scenes: [],
          props: [],
          scene_description: "雨夜，阿离立于屋檐下",
          utterances: [
            { kind: "voiceover", speaker: null, text: "三年后。" },
            { kind: "dialogue", speaker: "阿离", text: "你终于回来了。" },
          ],
          source_text: "三年后，阿离立于屋檐下：你终于回来了。",
        },
      ],
    },
    ...overrides,
  };
}

const NARRATION_SEGMENT = {
  segment_id: "E1S01",
  novel_text: "裴与出征后的第二年。",
  duration_seconds: 6,
  segment_break: false,
  characters_in_segment: ["裴与"],
  scenes: [],
  props: [],
};

function draftView(overrides: Partial<ScriptReviewQuarantine> = {}): ScriptReviewQuarantine {
  return {
    doc_type: "narration_script_plan",
    revision: "rev-1",
    editable_by: "user",
    content: null,
    violations: [],
    soft_violations: [],
    formal_exists: true,
    ...overrides,
  };
}

function narrationState(overrides: Partial<ScriptReviewState> = {}): ScriptReviewState {
  return {
    episode: 1,
    content_mode: "narration",
    status: "pending_review",
    fingerprint: "fp1",
    confirmed_at: null,
    quarantine: null,
    supported_durations: null,
    duration_tiers: null,
    episode_target_duration: null,
    script_overwrite: null,
    content: {
      segments: [
        {
          segment_id: "E1S01",
          novel_text: "裴与出征后的第二年。",
          duration_seconds: 6,
          segment_break: false,
          characters_in_segment: ["裴与"],
          scenes: [],
          props: [],
        },
      ],
    },
    ...overrides,
  };
}

describe("ScriptReviewGate", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders drama structured content with utterances and pending status", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState());
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);

    await waitFor(() => expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument());
    expect(screen.getByDisplayValue("阿离")).toBeInTheDocument();
    expect(screen.getByText("S01")).toBeInTheDocument();
    expect(screen.getByText("待确认")).toBeInTheDocument();
    expect(screen.getByText("确认并继续")).toBeInTheDocument();
  });

  it("confirms and reflects the unlocked state", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState());
    const confirm = vi
      .spyOn(API, "confirmScriptReview")
      .mockResolvedValue(dramaState(CONFIRMED));

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    await waitFor(() => expect(screen.getByText("确认并继续")).toBeInTheDocument());

    fireEvent.click(screen.getByText("确认并继续"));

    await waitFor(() => expect(confirm).toHaveBeenCalledWith("p", 1, {}));
    await waitFor(() =>
      expect(screen.getByText("内容已确认，此处只读。请在时间线上修改；要整集重做，请重跑脚本规划后再确认。")).toBeInTheDocument(),
    );
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("renders confirmed drama content without edit controls and offers the timeline", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      dramaState(CONFIRMED),
    );
    const openTimeline = vi.fn();

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" onOpenTimeline={openTimeline} />);

    expect(await screen.findByText("你终于回来了。")).toBeInTheDocument();
    expect(screen.getByText("三年后，阿离立于屋檐下：你终于回来了。")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除发声" })).not.toBeInTheDocument();
    expect(screen.getByText("内容已确认，此处只读。请在时间线上修改；要整集重做，请重跑脚本规划后再确认。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "去时间线修改" }));
    expect(openTimeline).toHaveBeenCalledTimes(1);
  });

  it("renders confirmed narration text read-only", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      narrationState(CONFIRMED),
    );

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" />);

    expect(await screen.findByText("裴与出征后的第二年。")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "去时间线修改" })).not.toBeInTheDocument();
  });

  it("turns read-only when a save is refused because the episode was confirmed meanwhile", async () => {
    vi.spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(dramaState())
      .mockResolvedValue(dramaState(CONFIRMED));
    vi.spyOn(API, "saveScriptReviewContent").mockRejectedValue(
      new ApiRequestError("脚本规划已确认，不能再修改", { code: "script_plan_confirmed" }, 409),
    );

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    fireEvent.change(await screen.findByDisplayValue("你终于回来了。"), { target: { value: "我的本地编辑" } });
    fireEvent.click(await screen.findByText("修复后保存"));

    await waitFor(() => expect(screen.queryByRole("textbox")).not.toBeInTheDocument());
    expect(screen.getByText("你终于回来了。")).toBeInTheDocument();
    expect(screen.queryByText("修复后保存")).not.toBeInTheDocument();
  });

  it("becomes editable again once a re-run script plan is pending review", async () => {
    vi.spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(dramaState(CONFIRMED))
      .mockResolvedValueOnce(dramaState({ fingerprint: "fp2" }));

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    await screen.findByText("你终于回来了。");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();

    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_script_plan"]);
    });

    expect(await screen.findByDisplayValue("你终于回来了。")).toBeInTheDocument();
  });

  it("lets a confirmed episode without a formal script confirm again to build it", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState({ ...CONFIRMED, script_overwrite: null }));
    const confirm = vi.spyOn(API, "confirmScriptReview").mockResolvedValue(dramaState(CONFIRMED));

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);

    const button = await screen.findByRole("button", { name: "重新确认并生成正式脚本" });
    expect(button).toBeEnabled();
    expect(screen.getByText("内容已确认，但本集还没有正式脚本。重新确认即按脚本规划转出正式脚本。")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();

    fireEvent.click(button);

    await waitFor(() => expect(confirm).toHaveBeenCalledWith("p", 1, {}));
    expect(await screen.findByRole("button", { name: "已确认" })).toBeDisabled();
  });

  it("keeps the ordinary confirm button when the episode has no formal script", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState());

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);

    expect(await screen.findByRole("button", { name: "确认并继续" })).toBeEnabled();
    expect(screen.queryByText("确认并覆盖正式脚本")).not.toBeInTheDocument();
  });

  it("renders a danger confirm that lists the consequences before overwriting a formal script", async () => {
    const overwrite = makeScriptOverwrite({
      revision: "sha256-v1:listed",
      entries: [
        makeScriptOverwriteEntry("E1S01", {
          has_storyboard: true,
          has_video: true,
          has_narration_audio: true,
          has_end_frame: true,
          grid_id: "grid_a",
        }),
        makeScriptOverwriteEntry("E1S09"),
      ],
      storyboard_count: 1,
      video_count: 1,
      narration_audio_count: 1,
      end_frame_count: 1,
      grid_member_count: 1,
      grid_count: 1,
      text: "现有 2 条分镜全部移除。\n以下内容无法在项目内恢复：分镜图 1 张、视频 1 段、配音 1 段、尾帧 1 张、宫格归属 1 处。\n将被移除的分镜：E1S01（分镜图、视频、配音、尾帧、宫格）、E1S09",
    });
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState({ script_overwrite: overwrite }));
    const confirm = vi
      .spyOn(API, "confirmScriptReview")
      .mockResolvedValue(dramaState(CONFIRMED));

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);

    const button = await screen.findByRole("button", { name: "确认并覆盖正式脚本" });
    expect(screen.getByText("本集已有正式脚本，确认会按脚本规划整份重建它。")).toBeInTheDocument();

    fireEvent.click(button);

    const dialog = await screen.findByRole("alertdialog");
    // 确认框原样呈现服务端生成的丢失清单，不在前端另拼。
    expect(within(dialog).getByText(itemIdsInEpisodeText(overwrite.text), { normalizer: (text) => text })).toBeInTheDocument();
    expect(confirm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "覆盖并确认" }));

    await waitFor(() => expect(confirm).toHaveBeenCalledWith("p", 1, { overwriteRevision: "sha256-v1:listed" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });

  it("switches to the danger confirm when the server reports an existing formal script", async () => {
    const overwrite = makeScriptOverwrite({
      revision: "sha256-v1:listed",
      entries: [makeScriptOverwriteEntry("E1S01")],
      text: "现有 1 条分镜全部移除。",
    });
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState());
    vi.spyOn(API, "confirmScriptReview").mockRejectedValue(
      new ApiRequestError("本集已有正式脚本", { script_overwrite: overwrite }, 409),
    );

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    fireEvent.click(await screen.findByRole("button", { name: "确认并继续" }));

    expect(await screen.findByRole("button", { name: "确认并覆盖正式脚本" })).toBeEnabled();
  });

  it("keeps the overwrite dialog open with the refreshed list when the formal script changed meanwhile", async () => {
    const listed = makeScriptOverwrite({
      revision: "sha256-v1:listed",
      entries: [makeScriptOverwriteEntry("E1S01")],
      text: "现有 1 条分镜全部移除。",
    });
    const refreshed = makeScriptOverwrite({
      revision: "sha256-v1:refreshed",
      entries: [makeScriptOverwriteEntry("E1S01"), makeScriptOverwriteEntry("E1S07", { has_storyboard: true })],
      storyboard_count: 1,
      text: "现有 2 条分镜全部移除。分镜图 1 张。E1S07（分镜图）",
    });
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState({ script_overwrite: listed }));
    const confirm = vi
      .spyOn(API, "confirmScriptReview")
      .mockRejectedValueOnce(new ApiRequestError("正式脚本已变化", { script_overwrite: refreshed }, 409))
      .mockResolvedValueOnce(dramaState(CONFIRMED));

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    fireEvent.click(await screen.findByRole("button", { name: "确认并覆盖正式脚本" }));
    fireEvent.click(await screen.findByRole("button", { name: "覆盖并确认" }));

    await waitFor(() => expect(screen.getByRole("alertdialog")).toHaveTextContent("S07"));
    expect(screen.getByRole("alertdialog")).toHaveTextContent(itemIdsInEpisodeText(refreshed.text));

    fireEvent.click(screen.getByRole("button", { name: "覆盖并确认" }));
    await waitFor(() => expect(confirm).toHaveBeenLastCalledWith("p", 1, { overwriteRevision: "sha256-v1:refreshed" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });

  it.each([400, 422])("warns and disables confirm when capabilities return %i", async (status) => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState());
    vi.spyOn(API, "getVideoCapabilities").mockRejectedValue(new ApiRequestError("无法解析", undefined, status));

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);

    const notice = await screen.findByRole("alert");
    expect(notice).toHaveTextContent("尚未配置可用的视频模型");
    expect(screen.getByRole("link", { name: "前往项目设置" })).toHaveAttribute("href", "/app/projects/p/settings?tab=models");
    const button = screen.getByRole("button", { name: "确认并继续" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", expect.stringContaining("尚未配置可用的视频模型"));
  });

  it("disables the overwrite confirm too when the video model cannot be resolved", async () => {
    const overwrite = makeScriptOverwrite({ revision: "sha256-v1:listed" });
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState({ script_overwrite: overwrite }));
    vi.spyOn(API, "getVideoCapabilities").mockRejectedValue(new ApiRequestError("无法解析", undefined, 422));

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);

    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "确认并覆盖正式脚本" })).toBeDisabled();
  });

  it("disables the in-dialog confirm when the video model turns out unresolvable after the dialog opened", async () => {
    const overwrite = makeScriptOverwrite({ revision: "sha256-v1:listed" });
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState({ script_overwrite: overwrite }));
    let rejectCapabilities: (reason: unknown) => void = () => {};
    vi.spyOn(API, "getVideoCapabilities").mockReturnValue(
      new Promise<VideoCapabilities>((_resolve, reject) => {
        rejectCapabilities = reject;
      }),
    );

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    fireEvent.click(await screen.findByRole("button", { name: "确认并覆盖正式脚本" }));
    expect(await screen.findByRole("button", { name: "覆盖并确认" })).toBeEnabled();

    await act(async () => {
      rejectCapabilities(new ApiRequestError("无法解析", undefined, 422));
    });

    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "覆盖并确认" })).toBeDisabled();
  });

  it("shows no video model warning once capabilities resolve", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState());
    const capabilities = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(VIDEO_CAPS);

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);

    const button = await screen.findByRole("button", { name: "确认并继续" });
    await waitFor(() => expect(capabilities).toHaveBeenCalled());
    await settleCapabilityRequests(capabilities);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(button).toBeEnabled();
  });

  it("keeps confirm available when the capability request itself fails", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState());
    const capabilities = vi.spyOn(API, "getVideoCapabilities").mockRejectedValue(new TypeError("Failed to fetch"));

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);

    const button = await screen.findByRole("button", { name: "确认并继续" });
    await waitFor(() => expect(capabilities).toHaveBeenCalled());
    await settleCapabilityRequests(capabilities);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(button).toBeEnabled();
  });

  it("edits content, surfaces save, and persists the edited intermediate", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState());
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(dramaState());

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    await waitFor(() => expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument());

    fireEvent.change(screen.getByDisplayValue("你终于回来了。"), { target: { value: "你怎么才回来。" } });
    // 编辑后出现保存按钮
    const saveBtn = await screen.findByText("修复后保存");
    fireEvent.click(saveBtn);

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    const [, , savedContent, baseFingerprint] = save.mock.calls[0];
    expect(savedContent).toMatchObject({
      scenes: [{ utterances: [{ text: "三年后。" }, { text: "你怎么才回来。" }] }],
    });
    // 保存携带 GET 时拿到的内容指纹，供服务端做并发编辑冲突比对
    expect(baseFingerprint).toBe("fp1");
  });

  it("compares the episode total against the project target", async () => {
    // dramaState 只有一个 8 秒场景，目标 30 秒 → 未超出，用中性对比文案
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState({ episode_target_duration: 30 }));
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);

    await waitFor(() => expect(screen.getByText("本集合计 8 秒 / 目标 30 秒")).toBeInTheDocument());
  });

  it("flags an over-target episode without blocking confirmation", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState({ episode_target_duration: 5 }));
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);

    await waitFor(() =>
      expect(screen.getByText(/本集合计 8 秒，比目标 5 秒多 3 秒/)).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "确认并继续" })).toBeEnabled();
  });

  it("shows no comparison when the project has no target", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState());
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);

    await waitFor(() => expect(screen.getByText("S01")).toBeInTheDocument());
    expect(screen.queryByText(/目标/)).not.toBeInTheDocument();
  });

  it("renders narration novel_text as editable", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(narrationState());
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" />);

    await waitFor(() => expect(screen.getByDisplayValue("裴与出征后的第二年。")).toBeInTheDocument());
    expect(screen.getByText("S01")).toBeInTheDocument();
  });

  it("presents a draft needing fixes with violations on their items and adopts it once a hand fix clears them", async () => {
    const draftSegment = { ...NARRATION_SEGMENT, novel_text: "" };
    const get = vi.spyOn(API, "getScriptReview").mockResolvedValue(
      narrationState({
        quarantine: draftView({
          content: { segments: [draftSegment] },
          violations: [
            { code: "blank_novel_text", label: "segment E1S01", message: "segment E1S01 的原文为空", line: null, item_index: 0 },
            { code: "coverage_gap", label: "", message: "源文末尾有一段未被任何分镜覆盖", line: null },
          ],
        }),
      }),
    );
    const save = vi
      .spyOn(API, "saveEpisodeDraft")
      .mockResolvedValue({ episode: 1, doc_type: "narration_script_plan", adopted: true, draft: null });
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" />);

    // 违约挂在所在分镜卡上，整集层面的违约置顶；面板呈现的是草稿而非正式内容，也不提供确认。
    await waitFor(() => expect(screen.getByText("segment S01 的原文为空")).toBeInTheDocument());
    expect(screen.getByText("源文末尾有一段未被任何分镜覆盖")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("裴与出征后的第二年。")).not.toBeInTheDocument();
    expect(screen.queryByText("确认并继续")).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: "小说原文" }), { target: { value: "补上的原文。" } });
    fireEvent.click(screen.getByRole("button", { name: /保存并校验/ }));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith(
        "p",
        1,
        "narration_script_plan",
        { segments: [{ ...draftSegment, novel_text: "补上的原文。" }] },
        "rev-1",
      ),
    );
    // 采用后重新拉取审核态：正式内容已变、草稿已不在。
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  });

  it("keeps the draft with the refreshed violations when a save still violates", async () => {
    const stillViolating = draftView({
      revision: "rev-2",
      content: { segments: [{ ...NARRATION_SEGMENT, novel_text: "仍然不对。" }] },
      violations: [{ code: "blank_novel_text", label: "", message: "仍有一处违约", line: null, item_index: 0 }],
    });
    vi.spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(narrationState({ quarantine: draftView({ content: { segments: [NARRATION_SEGMENT] } }) }))
      .mockResolvedValue(narrationState({ quarantine: stillViolating }));
    vi.spyOn(API, "saveEpisodeDraft").mockResolvedValue({
      episode: 1,
      doc_type: "narration_script_plan",
      adopted: false,
      draft: { ...stillViolating, episode: 1, item_ids: null },
    });
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" />);

    await waitFor(() => expect(screen.getByDisplayValue("裴与出征后的第二年。")).toBeInTheDocument());
    fireEvent.change(screen.getByRole("textbox", { name: "小说原文" }), { target: { value: "仍然不对。" } });
    fireEvent.click(screen.getByRole("button", { name: /保存并校验/ }));

    await waitFor(() => expect(screen.getByText("仍有一处违约")).toBeInTheDocument());
    expect(screen.getByDisplayValue("仍然不对。")).toBeInTheDocument();
  });

  it("falls back to presenting violations when the draft's new assets carry an unknown type or decision", async () => {
    const broken = { name: "宝剑", reason: "", description: "", target: "", asset_name: "", aliases: [] };
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      narrationState({
        quarantine: draftView({
          content: {
            segments: [NARRATION_SEGMENT],
            new_assets: [
              { ...broken, type: "product", decision: "register" },
              { ...broken, type: "prop", decision: "keep" },
            ],
          },
          violations: [{ code: "invalid_new_assets", label: "", message: "新增资产的类型不对", line: null }],
        }),
      }),
    );
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" />);

    await waitFor(() => expect(screen.getByText("新增资产的类型不对")).toBeInTheDocument());
    expect(screen.queryByDisplayValue("裴与出征后的第二年。")).not.toBeInTheDocument();
  });

  it("queues an AI repair of the saved draft with one-off instructions and adopts once the task clears the violations", async () => {
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    const get = vi.spyOn(API, "getScriptReview").mockResolvedValue(
      narrationState({
        quarantine: draftView({
          content: { segments: [NARRATION_SEGMENT] },
          violations: [
            { code: "duration_off_tier", label: "segment E1S01", message: "segment E1S01 的时长 5 不在模型档位内", line: null, item_index: 0 },
          ],
        }),
      }),
    );
    const resourceId = "episode-1-narration_script_plan";
    const repair = vi.spyOn(API, "repairEpisodeDraft").mockResolvedValue({
      batch: { batch_id: "b-1", members: [{ unit_id: resourceId, task_id: "t-repair" }] },
    });
    const repairTask = (overrides: Partial<TaskItem>) =>
      makeTask({
        task_id: "t-repair",
        project_name: "p",
        task_type: "text_draft_repair",
        resource_id: resourceId,
        ...overrides,
      });
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" />);

    // 有未保存的修改时不可用：AI 修复读取的是已保存的草稿。
    await waitFor(() => expect(screen.getByDisplayValue("裴与出征后的第二年。")).toBeInTheDocument());
    fireEvent.change(screen.getByRole("textbox", { name: "小说原文" }), { target: { value: "改了一半。" } });
    expect(screen.getByRole("button", { name: /AI 修复/ })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "小说原文" }), { target: { value: "裴与出征后的第二年。" } });

    fireEvent.click(screen.getByRole("button", { name: /AI 修复/ }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "只调整时长" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /开始修复/ }));

    await waitFor(() => expect(repair).toHaveBeenCalledWith("p", 1, "narration_script_plan", "rev-1", "只调整时长"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // 任务排队或执行期间状态条显示修复中，其余入口锁住；任务落定前不重拉。
    act(() => useTasksStore.setState({ tasks: [repairTask({ status: "running" })] }));
    expect(screen.getByRole("button", { name: /修复中/ })).toBeDisabled();
    expect(get).toHaveBeenCalledTimes(1);

    act(() =>
      useTasksStore.setState({
        tasks: [repairTask({ status: "succeeded", result: { adopted: true, violation_count: 0 } })],
      }),
    );
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(useAppStore.getState().toast?.text).toBe("违约已清零，草稿已采用为正式内容");
  });

  it("reports a failed AI repair task with its reason", async () => {
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      narrationState({
        quarantine: draftView({
          content: { segments: [NARRATION_SEGMENT] },
          violations: [
            { code: "duration_off_tier", label: "segment E1S01", message: "segment E1S01 的时长 5 不在模型档位内", line: null, item_index: 0 },
          ],
        }),
      }),
    );
    const running = makeTask({
      task_id: "t-repair",
      project_name: "p",
      task_type: "text_draft_repair",
      resource_id: "episode-1-narration_script_plan",
      status: "running",
    });
    // 别处提交、页面打开时已在跑的修复同样跟踪到终态。
    useTasksStore.setState({ tasks: [running] });
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" />);
    await waitFor(() => expect(screen.getByRole("button", { name: /修复中/ })).toBeDisabled());

    act(() =>
      useTasksStore.setState({
        tasks: [{ ...running, status: "failed", error_message: "AI 修复没有完成，草稿未改动" }],
      }),
    );

    await waitFor(() => expect(useAppStore.getState().toast?.text).toBe("AI 修复没有完成，草稿未改动"));
    expect(screen.getByRole("button", { name: /AI 修复/ })).toBeEnabled();
  });

  it("hands a draft needing fixes to the assistant with its violations, or asks it to promote when none remain", async () => {
    const get = vi.spyOn(API, "getScriptReview").mockResolvedValue(
      narrationState({
        quarantine: draftView({
          content: { segments: [NARRATION_SEGMENT] },
          violations: [
            { code: "duration_off_tier", label: "segment E1S01", message: "segment E1S01 的时长 5 不在模型档位内", line: null, item_index: 0 },
          ],
        }),
      }),
    );
    const { unmount } = render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" />);

    // 面板默认就是开着的，先关掉才断得出这次点击真的打开了它。
    act(() => useAppStore.getState().setAssistantPanelOpen(false));
    fireEvent.click(await screen.findByRole("button", { name: /交给 Agent 修复/ }));
    const input = useAssistantStore.getState().input;
    expect(input).toContain("doc_type=narration_script_plan");
    expect(input).toContain("1. segment E1S01 的时长 5 不在模型档位内");
    expect(useAppStore.getState().assistantPanelOpen).toBe(true);
    unmount();

    get.mockResolvedValue(dramaState({ quarantine: draftView({ doc_type: "drama_script_plan", content: { title: "第一集", scenes: [] } }) }));
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    fireEvent.click(await screen.findByRole("button", { name: /交给 Agent 修复/ }));
    const promote = useAssistantStore.getState().input;
    expect(promote).toContain("promote_draft");
    expect(promote).toContain("doc_type=drama_script_plan");
    expect(promote).not.toContain("违约待修复");
  });

  it("shows only a status for the agent's editable draft, keeps the formal content read-only, and discards it on confirm", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      narrationState({ quarantine: draftView({ editable_by: "agent", content: null }) }),
    );
    const discard = vi
      .spyOn(API, "discardEpisodeDraft")
      .mockResolvedValue({ episode: 1, doc_type: "narration_script_plan", discarded: true });
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" />);

    await waitFor(() => expect(screen.getByText("Agent 有一份未完成的修改")).toBeInTheDocument());
    expect(screen.getByText("裴与出征后的第二年。")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByText("确认并继续")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "丢弃这份修改" }));
    // 确认框写明丢弃后回到哪份内容。
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("正式脚本规划");
    fireEvent.click(within(dialog).getByRole("button", { name: "丢弃这份修改" }));

    await waitFor(() => expect(discard).toHaveBeenCalledWith("p", 1, "narration_script_plan", "rev-1"));
  });

  it("discards only the draft version on screen and loads the newer one when it changed elsewhere", async () => {
    const newer = draftView({
      revision: "rev-2",
      content: { segments: [{ ...NARRATION_SEGMENT, novel_text: "别处更新的原文。" }] },
    });
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(narrationState({ quarantine: draftView({ content: { segments: [NARRATION_SEGMENT] } }) }))
      .mockResolvedValue(narrationState({ quarantine: newer }));
    const discard = vi
      .spyOn(API, "discardEpisodeDraft")
      .mockRejectedValue(new ApiRequestError("草稿已变化", { code: "revision_conflict" }, 409));
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" />);

    await waitFor(() => expect(screen.getByDisplayValue("裴与出征后的第二年。")).toBeInTheDocument());
    fireEvent.change(screen.getByRole("textbox", { name: "小说原文" }), { target: { value: "我的本地编辑" } });
    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_script_plan"]);
    });
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(screen.getByDisplayValue("我的本地编辑")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "丢弃草稿" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "丢弃草稿" }));

    await waitFor(() => expect(discard).toHaveBeenCalledWith("p", 1, "narration_script_plan", "rev-1"));
    await waitFor(() => expect(screen.getByDisplayValue("别处更新的原文。")).toBeInTheDocument());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("adopts externally edited (agent) content on refetch when the user has no edits", async () => {
    const edited = dramaState();
    (edited.content as { scenes: { utterances: { text: string }[] }[] }).scenes[0].utterances[1].text =
      "agent 改写后的台词";
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(dramaState())
      .mockResolvedValueOnce(edited);

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    await waitFor(() => expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument());

    // 模拟 agent 在外部改了 script_plan → revision 变 → 触发重新拉取
    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_script_plan"]);
    });

    await waitFor(() => expect(screen.getByDisplayValue("agent 改写后的台词")).toBeInTheDocument());
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("preserves the user's unsaved edits when an external refetch arrives", async () => {
    const serverEdited = dramaState();
    (serverEdited.content as { scenes: { utterances: { text: string }[] }[] }).scenes[0].utterances[1].text =
      "服务端覆盖文案";
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(dramaState())
      .mockResolvedValueOnce(serverEdited);

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    await waitFor(() => expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument());

    // 用户本地编辑，尚未保存
    fireEvent.change(screen.getByDisplayValue("你终于回来了。"), { target: { value: "我的本地编辑" } });
    await screen.findByText("修复后保存");

    // 外部刷新到来（agent 改 script_plan → revision 变）→ 应保留用户草稿、不被服务端内容覆盖
    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_script_plan"]);
    });

    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(screen.getByDisplayValue("我的本地编辑")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("服务端覆盖文案")).not.toBeInTheDocument();
  });

  it("shows an empty state when there is no script_plan content", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      dramaState({ status: "no_script_plan", content: null, fingerprint: null }),
    );
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    await waitFor(() => expect(screen.getByText("暂无脚本规划结果")).toBeInTheDocument());
  });

  it("offers AI script planning from the empty state, replacing an existing formal script only after confirmation", async () => {
    useScriptPlanStore.getState().close();
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      dramaState({ status: "no_script_plan", content: null, fingerprint: null, script_overwrite: makeScriptOverwrite() }),
    );
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    fireEvent.click(await screen.findByRole("button", { name: "AI 规划脚本" }));
    expect(useScriptPlanStore.getState().request).toEqual({ projectName: "p", episode: 1, replaces: "formal_script" });
  });

  it("renders a load-error state distinct from the empty state", async () => {
    vi.spyOn(API, "getScriptReview").mockRejectedValue(new Error("网络异常"));
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);

    await waitFor(() => expect(screen.getByText("无法加载脚本规划结果")).toBeInTheDocument());
    // 错误态展示服务端错误信息与重试入口，且不与空态文案混淆。
    expect(screen.getByText("网络异常")).toBeInTheDocument();
    expect(screen.getByText("重试")).toBeInTheDocument();
    expect(screen.queryByText("暂无脚本规划结果")).not.toBeInTheDocument();
  });

  it("surfaces an error with retry when a refetch fails after an empty state", async () => {
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(dramaState({ status: "no_script_plan", content: null, fingerprint: null }))
      .mockRejectedValue(new Error("刷新失败"));
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    await waitFor(() => expect(screen.getByText("暂无脚本规划结果")).toBeInTheDocument());

    // 空态无真实内容可保留：revision 静默刷新失败应进错误态（区别于空态）并给重试，不滞留在过时空态。
    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_script_plan"]);
    });

    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(screen.getByText("无法加载脚本规划结果")).toBeInTheDocument();
    expect(screen.getByText("重试")).toBeInTheDocument();
    expect(screen.queryByText("暂无脚本规划结果")).not.toBeInTheDocument();
  });

  it("keeps existing content when a silent refetch fails", async () => {
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(dramaState())
      .mockRejectedValue(new Error("刷新失败"));
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    await waitFor(() => expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument());

    // revision 触发静默刷新失败：应保留已加载内容，不闪错误态 / 空态。
    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_script_plan"]);
    });

    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument();
    expect(screen.queryByText("无法加载脚本规划结果")).not.toBeInTheDocument();
    expect(screen.queryByText("暂无脚本规划结果")).not.toBeInTheDocument();
  });

  it("retries after a load error and recovers to normal content", async () => {
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockRejectedValueOnce(new Error("网络异常"))
      .mockResolvedValue(dramaState());
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);

    await waitFor(() => expect(screen.getByText("重试")).toBeInTheDocument());

    fireEvent.click(screen.getByText("重试"));

    await waitFor(() => expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument());
    expect(screen.queryByText("无法加载脚本规划结果")).not.toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
  });
});

describe("ScriptReviewGate new assets", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
  });

  function withRegistered(characters: Record<string, { description: string }>) {
    useProjectsStore.setState({
      currentProjectData: { characters, scenes: {}, props: {} } as unknown as ProjectData,
    });
  }

  function newAsset(overrides: Partial<PlanNewAsset>): PlanNewAsset {
    return {
      type: "character",
      name: "将军",
      decision: "register",
      reason: "第一段首次出场",
      description: "银甲",
      aliases: [],
      target: "",
      asset_name: "",
      ...overrides,
    };
  }

  function stateWithNewAssets(items: PlanNewAsset[], overrides: Partial<ScriptReviewState> = {}) {
    const base = narrationState(overrides);
    const content = base.content as NarrationScriptPlanDraft;
    return {
      ...base,
      content: {
        ...content,
        segments: [{ ...content.segments[0], characters_in_segment: ["裴与", "将军"] }],
        new_assets: items,
      },
    };
  }

  it("lists each new asset with the AI's decision, its reason and where it appears", async () => {
    withRegistered({ 裴与: { description: "将军" } });
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      stateWithNewAssets([newAsset({}), newAsset({ name: "路人", decision: "skip", description: "" })]),
    );

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" />);

    expect(await screen.findByText("本集新增资产")).toBeInTheDocument();
    expect(screen.getByText("登记为新资产「将军」")).toBeInTheDocument();
    expect(screen.getByText("不登记，只用文字描述")).toBeInTheDocument();
    expect(screen.getAllByText("依据：第一段首次出场")).toHaveLength(2);
    expect(screen.getByText("出场 1 处")).toBeInTheDocument();
  });

  it("saves a decision changed to merging into a registered asset", async () => {
    withRegistered({ 裴与: { description: "将军" } });
    vi.spyOn(API, "getScriptReview").mockResolvedValue(stateWithNewAssets([newAsset({})]));
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(stateWithNewAssets([]));

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" />);
    fireEvent.change(await screen.findByLabelText("「将军」的处理方式"), { target: { value: "merge" } });
    fireEvent.change(screen.getByLabelText("归到"), { target: { value: "裴与" } });

    expect(screen.getByText("归到「裴与」，「将军」记为别名")).toBeInTheDocument();
    fireEvent.click(await screen.findByText("修复后保存"));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][2]).toMatchObject({ new_assets: [{ name: "将军", decision: "merge", target: "裴与" }] });
  });

  it.each(["register", "skip"] as const)(
    "explains that a new asset named like a registered one merges into it (%s)",
    async (decision) => {
      withRegistered({ 将军: { description: "银甲" } });
      vi.spyOn(API, "getScriptReview").mockResolvedValue(
        stateWithNewAssets([newAsset({ name: " 将军", decision })]),
      );

      render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" />);

      expect(
        await screen.findByText("1 项与已登记的同类资产同名，确认时自动归到该资产，不新建资产。"),
      ).toBeInTheDocument();
      expect(screen.getByText("与已登记的「将军」同名，确认时自动归到该资产")).toBeInTheDocument();
    },
  );

  it("shows the decisions read-only once confirmed", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(stateWithNewAssets([newAsset({})], CONFIRMED));

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" />);

    expect(await screen.findByText("登记为新资产「将军」")).toBeInTheDocument();
    expect(screen.queryByLabelText("「将军」的处理方式")).not.toBeInTheDocument();
  });
});

describe("ScriptReviewGate item fields", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
  });

  function withProject(data: Partial<ProjectData>) {
    useProjectsStore.setState({
      currentProjectData: { characters: {}, scenes: {}, props: {}, ...data } as unknown as ProjectData,
    });
  }

  function planAsset(overrides: Partial<PlanNewAsset>): PlanNewAsset {
    return {
      type: "character",
      name: "将军",
      decision: "register",
      reason: "首次出场",
      description: "银甲",
      aliases: [],
      target: "",
      asset_name: "",
      ...overrides,
    };
  }

  function narrationWith(
    segment: Partial<NarrationScriptPlanDraft["segments"][number]>,
    newAssets: PlanNewAsset[] = [],
    overrides: Partial<ScriptReviewState> = {},
  ): ScriptReviewState {
    return narrationState({
      content: { segments: [{ ...NARRATION_SEGMENT, ...segment }], new_assets: newAssets },
      ...overrides,
    });
  }

  async function saveAndReadContent(save: MockInstance<typeof API.saveScriptReviewContent>) {
    fireEvent.click(await screen.findByText("修复后保存"));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    return save.mock.calls[0][2];
  }

  it("picks a duration from the current tiers and saves it", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(narrationWith({}));
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(narrationWith({ duration_seconds: 8 }));

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" durationOptions={[4, 6, 8]} />);
    const select = await screen.findByLabelText("S01 时长");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["4 秒", "6 秒", "8 秒"]);
    fireEvent.change(select, { target: { value: "8" } });

    expect(await saveAndReadContent(save)).toMatchObject({ segments: [{ duration_seconds: 8 }] });
  });

  it("marks a duration outside the tiers in red with its cause and blocks confirming", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(narrationWith({ duration_seconds: 6 }));

    render(
      <ScriptReviewGate
        projectName="p"
        episode={1}
        contentMode="narration"
        videoModelUnresolved={false}
        durationOptions={[4, 8]}
        durationWarningReason={() => "resolution"}
      />,
    );

    expect(await screen.findByText("当前秒数 6 在当前分辨率下不可用，可选 [4, 8]")).toBeInTheDocument();
    const confirm = screen.getByRole("button", { name: "确认并继续" });
    expect(confirm).toBeDisabled();
    expect(confirm).toHaveAttribute("title", "有分镜的时长不在当前档位内，请改选后再确认");

    fireEvent.change(screen.getByLabelText("S01 时长"), { target: { value: "8" } });
    expect(screen.queryByText("当前秒数 6 在当前分辨率下不可用，可选 [4, 8]")).not.toBeInTheDocument();
    expect(confirm).toBeEnabled();
  });

  it("offers the planning tiers when the endpoint fixes the clip length, and blocks durations outside them", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(narrationWith({ duration_seconds: 6 }));
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue({
      ...VIDEO_CAPS,
      supported_durations: [],
      duration_endpoint_fixed: true,
      duration_constraints: { resolution: null, uses_reference_images: false, allowed: [], excluded: {}, planning: [4, 8] },
    });

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" />);

    const select = await screen.findByLabelText("S01 时长");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["4 秒", "6 秒", "8 秒"]);
    expect(screen.getByText("时长由端点固定：每段成片多长由 workflow 决定。")).toBeInTheDocument();
    expect(screen.getByText("当前秒数 6 不在模型支持范围 [4, 8] 内")).toBeInTheDocument();
    const confirm = screen.getByRole("button", { name: "确认并继续" });
    expect(confirm).toBeDisabled();

    fireEvent.change(select, { target: { value: "8" } });
    expect(confirm).toBeEnabled();
  });

  it("toggles the chapter break point and saves it", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(narrationWith({}));
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(narrationWith({ segment_break: true }));

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" durationOptions={[6]} />);
    fireEvent.click(await screen.findByRole("switch", { name: "设为章节切分点" }));

    expect(await saveAndReadContent(save)).toMatchObject({ segments: [{ segment_break: true }] });
  });

  it("offers registered assets and this episode's registered new assets as references, not skipped ones", async () => {
    withProject({ characters: { 裴与: { description: "将军" } } } as Partial<ProjectData>);
    const newAssets = [
      planAsset({ name: "小桃", description: "女童" }),
      planAsset({ name: "路人", decision: "skip", description: "" }),
      planAsset({ type: "scene", name: "旧宅", description: "破败院落" }),
    ];
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      narrationWith({ characters_in_segment: ["裴与", "路人"] }, newAssets),
    );
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(narrationWith({}));

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" durationOptions={[6]} />);
    fireEvent.click(await screen.findByRole("button", { name: "编辑引用" }));

    const dialog = await screen.findByRole("dialog");
    const xiaotao = within(dialog).getByRole("button", { name: /小桃/ });
    expect(within(xiaotao).getByText("本集新增")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /旧宅/ })).toBeInTheDocument();
    // 「不登记」的项不作候选；已选的那一项说明确认时会被移出，而不是失效引用。
    const passerby = within(dialog).getByRole("button", { name: /路人/ });
    expect(within(passerby).getByText("已选「不登记」，确认时从引用中移除")).toBeInTheDocument();

    fireEvent.click(xiaotao);
    fireEvent.click(within(dialog).getByRole("button", { name: /旧宅/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "确定" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    expect(await saveAndReadContent(save)).toMatchObject({
      segments: [{ characters_in_segment: ["裴与", "路人", "小桃"], scenes: ["旧宅"] }],
    });
  });

  it("suggests registered and new characters as speakers but still accepts other names", async () => {
    withProject({ characters: { 阿离: { description: "少女" } } } as Partial<ProjectData>);
    const state = dramaState();
    const content = state.content as NonNullable<ReturnType<typeof dramaState>["content"]> & { new_assets?: PlanNewAsset[] };
    vi.spyOn(API, "getScriptReview").mockResolvedValue({
      ...state,
      content: {
        ...content,
        new_assets: [planAsset({ name: "小桃" }), planAsset({ name: "路人", decision: "skip", description: "" })],
      },
    } as ScriptReviewState);
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(dramaState());

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" durationOptions={[8]} />);
    const speaker = await screen.findByDisplayValue("阿离");
    const listId = speaker.getAttribute("list");
    expect(listId).toBeTruthy();
    const options = [...document.getElementById(listId!)!.querySelectorAll("option")].map((o) => o.value);
    expect(options).toEqual(["阿离", "小桃"]);

    fireEvent.change(speaker, { target: { value: "店小二" } });
    expect(await saveAndReadContent(save)).toMatchObject({
      scenes: [{ utterances: [{ kind: "voiceover" }, { kind: "dialogue", speaker: "店小二" }] }],
    });
  });

  it("points structural edits to the timeline while pending and hides field controls once confirmed", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(narrationWith({}));
    const { unmount } = render(
      <ScriptReviewGate projectName="p" episode={1} contentMode="narration" durationOptions={[6]} />,
    );
    expect(await screen.findByText("确认后可在时间线增删、调整顺序")).toBeInTheDocument();
    unmount();

    vi.spyOn(API, "getScriptReview").mockResolvedValue(narrationWith({ scenes: ["旧宅"] }, [], CONFIRMED));
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" durationOptions={[6]} />);
    expect(await screen.findByText("旧宅")).toBeInTheDocument();
    expect(screen.queryByText("确认后可在时间线增删、调整顺序")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("S01 时长")).not.toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑引用" })).not.toBeInTheDocument();
  });
});
