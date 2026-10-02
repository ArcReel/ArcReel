import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Route } from "wouter";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type {
  EditClip,
  EditTimelinePreviewMedia,
  EditTimelineReadout,
  EditTimelineSummary,
} from "@/types/edit-timeline";

import { EditTimelineView } from "./EditTimelineView";

function summary(id: string, name: string, updatedAt: string, revision = 1): EditTimelineSummary {
  return {
    id,
    name,
    episode: 1,
    revision,
    clip_count: 3,
    created_at: "2026-09-30T08:00:00Z",
    updated_at: updatedAt,
    updated_by: { kind: "arcreel_agent", user_id: null },
    update_summary: "剪辑",
    agent_turn: null,
  };
}

function clip(overrides: Partial<EditClip> & Pick<EditClip, "id" | "unit_id" | "start" | "duration">): EditClip {
  return {
    status: "ready",
    video_version: 1,
    source_duration: 5,
    trim: null,
    source_volume: 1,
    hold: 0,
    carries_narration: false,
    narration: null,
    reason: null,
    transition_to_next: null,
    ...overrides,
  };
}

const INITIAL_CUT: EditTimelineReadout = {
  timeline: { id: "tl-00000002", name: "初剪", episode: 1 },
  revision: 3,
  latest_revision: 3,
  duration: 7.8,
  clips: [
    clip({ id: "c1", unit_id: "E1U1", start: 0, duration: 2.8, trim: { source_in: 1.2, source_out: 4, basis_version: 1 } }),
    clip({ id: "c2", unit_id: "E1U2", start: 2.8, duration: 0, status: "unit_deleted", video_version: null, source_duration: null }),
    clip({
      id: "c3",
      unit_id: "E1U3",
      start: 2.8,
      duration: 5,
      video_version: 2,
      trim: { source_in: 0.5, source_out: 2, basis_version: 1 },
      reason: "保留推门动作",
    }),
  ],
  bgm: [],
  issues: [
    { code: "unit_deleted", severity: "info", applies_to: "all", clip_ids: ["c2"], unit_id: "E1U2", params: {} },
    {
      code: "trim_ignored",
      severity: "info",
      applies_to: "all",
      clip_ids: ["c3"],
      unit_id: "E1U3",
      params: { basis_version: 1, current_version: 2 },
    },
    { code: "unit_unused", severity: "info", applies_to: "all", clip_ids: [], unit_id: "E1U4", params: {} },
  ],
};

const SCRIPT = { episode: 1, video_units: [{ unit_id: "E1U1" }, { unit_id: "E1U3" }, { unit_id: "E1U4" }] };

const NO_TIMELINE = () => <p>no timeline</p>;

function renderView(ttsNarration = true) {
  return render(
    <EditTimelineView
      projectName="demo"
      episode={1}
      script={SCRIPT}
      aspect="16:9"
      ttsNarration={ttsNarration}
      renderEmptyState={NO_TIMELINE}
    />,
  );
}

describe("EditTimelineView", () => {
  beforeEach(() => {
    useProjectsStore.setState({ projectSnapshotRevisions: {} });
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
    vi.spyOn(API, "getEditTimelinePreviewMedia").mockResolvedValue({
      timeline_id: "tl-00000002",
      revision: 3,
      narration: "without_narration",
      units: [],
      bgm: [],
    });
  });

  it("shows just now in the author line within a minute of the last edit", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [summary("tl-00000002", "初剪", new Date(Date.now() - 10_000).toISOString())],
    });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);

    renderView();

    expect(await screen.findByText("ArcReel Agent 刚刚修改")).toBeInTheDocument();
  });

  it("opens the most recently edited timeline and loads the first clip with the next one preloaded", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [
        summary("tl-00000001", "按脚本顺序", "2026-09-30T09:00:00Z"),
        summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3),
      ],
    });
    const read = vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);

    renderView();

    expect(await screen.findByRole("tab", { name: "初剪" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText(/ArcReel Agent 修改于/)).toBeInTheDocument();
    await screen.findByTestId("edit-clip-c1");
    expect(read).toHaveBeenCalledWith("demo", "tl-00000002", expect.anything());
    expect(screen.getByTestId("edit-player-video-0")).toHaveAttribute(
      "src",
      "/api/v1/files/demo/reference_videos/E1U1.mp4?v=1",
    );
    // 已删除单元的片段被跳过，空闲的元素预载下一个可播放片段
    expect(screen.getByTestId("edit-player-video-1")).toHaveAttribute(
      "src",
      "/api/v1/files/demo/reference_videos/E1U3.mp4?v=2",
    );
  });

  it("plays the source at the clip volume times the provider audio switch of its unit", async () => {
    const quiet: EditTimelineReadout = {
      ...INITIAL_CUT,
      clips: [
        { ...INITIAL_CUT.clips[0], source_volume: 0.6 },
        INITIAL_CUT.clips[1],
        { ...INITIAL_CUT.clips[2], source_volume: 0.5 },
      ],
    };
    const unit = (unit_id: string, provider_audio: boolean) => ({
      unit_id,
      provider_audio,
      narration_audio: null,
      subtitles_follow_narration: false,
      subtitles: [],
    });
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)],
    });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(quiet);
    vi.spyOn(API, "getEditTimelinePreviewMedia").mockResolvedValue({
      timeline_id: "tl-00000002",
      revision: 3,
      narration: "without_narration",
      units: [unit("E1U1", false), unit("E1U3", true)],
      bgm: [],
    });

    renderView();

    await waitFor(() => {
      expect((screen.getByTestId("edit-player-video-0") as HTMLVideoElement).volume).toBe(0);
    });
    expect((screen.getByTestId("edit-player-video-1") as HTMLVideoElement).volume).toBe(0.5);
  });

  it("marks stale trims, deleted footage and unused units, and lists them as issues", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)],
    });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);

    renderView();

    const stale = await screen.findByTestId("edit-clip-c3");
    expect(stale).toHaveAttribute("data-trim-ignored", "true");
    expect(screen.queryByTestId("edit-clip-c2")).not.toBeInTheDocument();
    expect(screen.getByTestId("edit-clip-deleted-c2")).toBeInTheDocument();
    expect(screen.getByText("未使用").parentElement).toHaveTextContent("U4");

    const issues = screen.getByRole("heading", { name: "问题（3）" }).parentElement as HTMLElement;
    expect(within(issues).getByText("c2：素材已删除，已跳过")).toBeInTheDocument();
    expect(within(issues).getByText("视频单元 U4 未使用")).toBeInTheDocument();

    fireEvent.click(within(issues).getByText("c3：素材已更新，暂用完整视频"));
    const inspector = screen.getByTestId("edit-clip-inspector");
    expect(within(inspector).getByText("0.5–2s")).toHaveClass("line-through");
    expect(inspector).toHaveTextContent("（素材共 5s）");
    expect(inspector).toHaveTextContent("素材已更新，暂用完整视频");
    expect(inspector).toHaveTextContent("保留推门动作");
  });

  it("words each narration issue by its cause", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)],
    });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue({
      ...INITIAL_CUT,
      issues: [
        {
          code: "narration_overrun",
          severity: "warning",
          applies_to: "with_narration",
          clip_ids: ["c1", "c3"],
          unit_id: "E1U1",
          params: { cause: "next_narration", next_unit_id: "E1U3", overlap: 0.5 },
        },
        {
          code: "narration_overrun",
          severity: "warning",
          applies_to: "with_narration",
          clip_ids: ["c3"],
          unit_id: "E1U3",
          params: { cause: "timeline_end", overflow: 1.2 },
        },
        {
          code: "narration_source_collision",
          severity: "warning",
          applies_to: "with_narration",
          clip_ids: ["c1", "c3"],
          unit_id: "E1U1",
          params: { cause: "dialogue", other_unit_id: "E1U3", source_volume: 1, overlap: 0.8 },
        },
        {
          code: "narration_missing",
          severity: "blocking",
          applies_to: "with_narration",
          clip_ids: ["c3"],
          unit_id: "E1U3",
          params: {},
        },
      ],
    });

    renderView();

    const issues = (await screen.findByRole("heading", { name: "问题（4）" })).parentElement as HTMLElement;
    expect(within(issues).getByText("c1、c3：旁白与下一段旁白重叠 0.5s")).toBeInTheDocument();
    expect(within(issues).getByText("c3：旁白超出时间线末尾 1.2s")).toBeInTheDocument();
    expect(within(issues).getByText("c1、c3：旁白延伸到台词片段上 0.8s，可能与原声相撞")).toBeInTheDocument();
    expect(within(issues).getByText("c3：视频单元 U3 还没有旁白配音，带旁白版本无法出片")).toBeInTheDocument();
  });

  it("lists a missing BGM as plain text, since a BGM clip is not a timeline clip to select", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)],
    });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue({
      ...INITIAL_CUT,
      bgm: [
        { id: "b1", bgm_id: "bgm-0009", name: null, start: 0, end: 7.8, source_in: 0, source_out: 30, volume: 1, fade_in: 0, fade_out: 0 },
      ],
      issues: [
        {
          code: "bgm_missing",
          severity: "blocking",
          applies_to: "all",
          clip_ids: ["b1"],
          unit_id: null,
          params: { bgm_id: "bgm-0009" },
        },
      ],
    });

    renderView();

    const issues = (await screen.findByRole("heading", { name: "问题（1）" })).parentElement as HTMLElement;
    const text = within(issues).getByText("b1：引用的 BGM bgm-0009 已不在项目里，无法出片");
    expect(text.closest("button")).toBeNull();
  });

  it("shows the new revision after the project reports a change", async () => {
    const list = vi
      .spyOn(API, "listEditTimelines")
      .mockResolvedValue({ timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)] });
    const read = vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);

    renderView();
    await screen.findByTestId("edit-clip-c3");

    list.mockResolvedValue({ timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:05:00Z", 4)] });
    read.mockResolvedValue({
      ...INITIAL_CUT,
      revision: 4,
      latest_revision: 4,
      duration: 4.8,
      clips: [INITIAL_CUT.clips[0], clip({ id: "c4", unit_id: "E1U4", start: 2.8, duration: 2 })],
      issues: [],
    });
    act(() => useProjectsStore.setState({ projectSnapshotRevisions: { demo: 1 } }));

    expect(await screen.findByTestId("edit-clip-c4")).toBeInTheDocument();
    expect(screen.queryByTestId("edit-clip-c3")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "问题（0）" })).toBeInTheDocument();
  });

  it("keeps the last preview when a refresh fails", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)],
    });
    const read = vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);

    renderView();
    await screen.findByTestId("edit-clip-c3");

    read.mockRejectedValue(new Error("probe failed"));
    act(() => useProjectsStore.setState({ projectSnapshotRevisions: { demo: 1 } }));

    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    await act(async () => {});
    expect(screen.getByTestId("edit-clip-c3")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps the timeline being watched when another one is created", async () => {
    const list = vi
      .spyOn(API, "listEditTimelines")
      .mockResolvedValue({ timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)] });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);

    renderView();
    await screen.findByTestId("edit-clip-c3");

    list.mockResolvedValue({
      timelines: [
        summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3),
        summary("tl-00000003", "快节奏版", "2026-09-30T11:00:00Z"),
      ],
    });
    act(() => useProjectsStore.setState({ projectSnapshotRevisions: { demo: 1 } }));

    expect(await screen.findByRole("tab", { name: "快节奏版" })).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("tab", { name: "初剪" })).toHaveAttribute("aria-selected", "true");
  });

  it("switches timelines with the arrow keys and labels the preview with the selected tab", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [
        summary("tl-00000001", "按脚本顺序", "2026-09-30T09:00:00Z"),
        summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3),
      ],
    });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);

    renderView();

    const cut = await screen.findByRole("tab", { name: "初剪" });
    expect(screen.getByRole("tabpanel", { name: "初剪" })).toBeInTheDocument();
    expect(cut).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "按脚本顺序" })).toHaveAttribute("tabindex", "-1");

    fireEvent.keyDown(cut, { key: "ArrowRight" });
    const byScript = screen.getByRole("tab", { name: "按脚本顺序" });
    expect(byScript).toHaveAttribute("aria-selected", "true");
    expect(byScript).toHaveFocus();
    expect(screen.getByRole("tabpanel", { name: "按脚本顺序" })).toBeInTheDocument();

    fireEvent.keyDown(byScript, { key: "ArrowLeft" });
    expect(screen.getByRole("tab", { name: "初剪" })).toHaveFocus();
  });

  it("hands the current timeline and its issues to the actions area", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)],
    });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);
    Element.prototype.scrollIntoView = vi.fn();

    render(
      <EditTimelineView
        projectName="demo"
        episode={1}
        script={SCRIPT}
        aspect="16:9"
        ttsNarration
        renderEmptyState={NO_TIMELINE}
        renderActions={({ timelineId, timelineName, issues, showIssues }) => (
          <button type="button" onClick={showIssues}>
            {`${timelineName} ${timelineId} ${issues === null ? "…" : issues.length}`}
          </button>
        )}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "初剪 tl-00000002 3" }));
    expect(screen.getByRole("heading", { name: "问题（3）" })).toHaveFocus();
  });

  it("lets the empty state reload the list after a timeline is created", async () => {
    const list = vi.spyOn(API, "listEditTimelines").mockResolvedValue({ timelines: [] });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);

    render(
      <EditTimelineView
        projectName="demo"
        episode={1}
        script={SCRIPT}
        aspect="16:9"
        ttsNarration
        renderEmptyState={({ reload }) => (
          <button type="button" onClick={reload}>
            新建
          </button>
        )}
      />,
    );

    list.mockResolvedValue({ timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)] });
    fireEvent.click(await screen.findByRole("button", { name: "新建" }));

    expect(await screen.findByRole("tab", { name: "初剪" })).toHaveAttribute("aria-selected", "true");
  });

  it("marks missing narration audio with a dashed placeholder in a TTS project but not in a post-production one", async () => {
    const missing: EditTimelineReadout = {
      ...INITIAL_CUT,
      clips: [
        { ...INITIAL_CUT.clips[0], carries_narration: true, narration: { start: 0, end: null } },
        INITIAL_CUT.clips[1],
        INITIAL_CUT.clips[2],
      ],
    };
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)],
    });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(missing);

    const tts = renderView(true);
    const dashed = await screen.findByTestId("edit-narration-c1");
    expect(dashed).toHaveAttribute("data-missing-audio", "true");
    expect(dashed).not.toHaveAttribute("data-post-production");
    expect(dashed).toHaveAttribute("title", "U1 还没有旁白配音，挂在 c1 上");
    tts.unmount();

    renderView(false);
    const neutral = await screen.findByTestId("edit-narration-c1");
    expect(neutral).not.toHaveAttribute("data-missing-audio");
    expect(neutral).toHaveAttribute("data-post-production", "true");
    expect(neutral).toHaveAttribute("title", "U1 的旁白由后期配音，预览不出声，挂在 c1 上");
    expect(neutral).not.toHaveClass("border-dashed");
  });

  it("shows narration over the clips it runs across, subtitles that can be hidden, and BGM once there is any", async () => {
    // c1 的旁白 4.5s，越过 c1 的出点延伸到 c3 上；c1 → c3 叠化
    const narrated: EditTimelineReadout = {
      ...INITIAL_CUT,
      clips: [
        { ...INITIAL_CUT.clips[0], carries_narration: true, narration: { start: 0, end: 4.5 }, transition_to_next: { type: "dissolve", duration: 1 } },
        INITIAL_CUT.clips[1],
        INITIAL_CUT.clips[2],
      ],
      bgm: [
        { id: "b1", bgm_id: "bgm-0001", name: "雨夜", start: 0, end: 7.8, source_in: 0, source_out: 30, volume: 0.25, fade_in: 1, fade_out: 1 },
      ],
    };
    const media: EditTimelinePreviewMedia = {
      timeline_id: "tl-00000002",
      revision: 3,
      narration: "with_narration",
      units: [
        {
          unit_id: "E1U1",
          provider_audio: true,
          narration_audio: { path: "audio/E1U1.mp3", version: 2 },
          subtitles_follow_narration: true,
          subtitles: [{ start: 0, duration: 4.5, text: "门后传来脚步声。" }],
        },
      ],
      bgm: [{ bgm_id: "bgm-0001", path: "bgm/bgm-0001.mp3", gain: 1 }],
    };
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)],
    });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(narrated);
    vi.spyOn(API, "getEditTimelinePreviewMedia").mockResolvedValue(media);

    renderView();

    const span = await screen.findByTestId("edit-narration-c1");
    expect(span).toHaveAttribute("title", "U1 的旁白，挂在 c1 上：0–4.5s");
    // 4.5 / 7.8 ≈ 57.69%，比 c1 自身的 2.8s 宽
    expect(span.style.width).toContain("57.69");
    expect(await screen.findByTestId("edit-player-subtitle")).toHaveTextContent("门后传来脚步声。");
    expect(screen.getByTestId("edit-bgm-b1")).toHaveAttribute(
      "title",
      "雨夜：0–7.8s，音量 0.25，淡入 1s，淡出 1s",
    );
    expect(screen.getByText("转场效果以成片为准")).toBeInTheDocument();

    await waitFor(() => expect(screen.getByRole("button", { name: "播放" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "播放" }));
    const sources = vi
      .mocked(HTMLMediaElement.prototype.play)
      .mock.contexts.filter((media): media is HTMLAudioElement => media instanceof HTMLAudioElement)
      .map((media) => media.getAttribute("src"));
    expect(sources).toEqual(expect.arrayContaining([expect.stringContaining("bgm/bgm-0001.mp3")]));

    const toggle = screen.getByRole("button", { name: "字幕" });
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByTestId("edit-player-subtitle")).not.toBeInTheDocument();
  });

  it("keeps an empty BGM track with its upload entry and hides the transition note when the timeline has neither", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)],
    });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);

    renderView();

    await screen.findByTestId("edit-clip-c1");
    expect(screen.getByText("旁白")).toBeInTheDocument();
    expect(screen.getByText("暂无 BGM 片段。上传 BGM 后，可以让 Agent 摆进时间线")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传 BGM" })).toBeEnabled();
    expect(screen.queryByText("转场效果以成片为准")).not.toBeInTheDocument();
  });

  it("uploads BGM from the BGM track without moving the playhead and reports the result", async () => {
    useAppStore.setState({ toast: null });
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)],
    });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);
    const upload = vi.spyOn(API, "uploadBgm").mockResolvedValue({
      success: true,
      bgm: { id: "bgm-0001", name: "雨夜", duration: 30, gain: 1, path: "bgm/bgm-0001.mp3", url: "/api/v1/files/demo/bgm/bgm-0001.mp3" },
    });

    renderView();

    await screen.findByTestId("edit-clip-c1");
    const tracks = screen.getByRole("group", { name: "时间线轨道，点击跳到对应时间" });
    vi.spyOn(tracks, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 780, 100));
    const button = screen.getByRole("button", { name: "上传 BGM" });
    fireEvent.pointerDown(button, { button: 0, clientX: 390 });
    expect(parseFloat(screen.getByTestId("edit-playhead").style.left)).toBe(0);

    const file = new File(["mp3"], "雨夜.mp3", { type: "audio/mpeg" });
    fireEvent.change(screen.getByTestId("edit-bgm-upload-input"), { target: { files: [file] } });

    await waitFor(() => expect(useAppStore.getState().toast?.tone).toBe("success"));
    expect(upload).toHaveBeenCalledWith("demo", file);
    expect(useAppStore.getState().toast?.text).toContain("雨夜");
  });

  it("reports a refused BGM upload with the server's reason", async () => {
    useAppStore.setState({ toast: null });
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)],
    });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);
    vi.spyOn(API, "uploadBgm").mockRejectedValue(new Error("这首 BGM 几乎没有声音"));

    renderView();

    await screen.findByTestId("edit-clip-c1");
    fireEvent.change(screen.getByTestId("edit-bgm-upload-input"), {
      target: { files: [new File(["wav"], "silence.wav", { type: "audio/wav" })] },
    });

    await waitFor(() => expect(useAppStore.getState().toast?.tone).toBe("error"));
    expect(useAppStore.getState().toast?.text).toBe("BGM 上传失败：这首 BGM 几乎没有声音");
    expect(screen.getByRole("button", { name: "上传 BGM" })).toBeEnabled();
  });

  it("unlocks every narration on play but only loads the ones about to be heard", async () => {
    // c2 的旁白从 20s 开始，离播放起点超过预载窗口
    const long: EditTimelineReadout = {
      ...INITIAL_CUT,
      duration: 25,
      clips: [
        clip({ id: "c1", unit_id: "E1U1", start: 0, duration: 20, carries_narration: true, narration: { start: 0, end: 3 } }),
        clip({ id: "c2", unit_id: "E1U3", start: 20, duration: 5, carries_narration: true, narration: { start: 20, end: 23 } }),
      ],
      issues: [],
    };
    const narrationOf = (unit_id: string) => ({
      unit_id,
      provider_audio: true,
      narration_audio: { path: `audio/${unit_id}.mp3`, version: 1 },
      subtitles_follow_narration: true,
      subtitles: [],
    });
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)],
    });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(long);
    vi.spyOn(API, "getEditTimelinePreviewMedia").mockResolvedValue({
      timeline_id: "tl-00000002",
      revision: 3,
      narration: "with_narration",
      units: [narrationOf("E1U1"), narrationOf("E1U3")],
      bgm: [],
    });

    renderView();

    await screen.findByTestId("edit-narration-c2");
    await waitFor(() => expect(screen.getByRole("button", { name: "播放" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "播放" }));

    const unlocked = vi
      .mocked(HTMLMediaElement.prototype.play)
      .mock.contexts.filter((media): media is HTMLAudioElement => media instanceof HTMLAudioElement);
    expect(unlocked).toHaveLength(2);
    expect(unlocked.map((media) => media.getAttribute("src"))).toEqual([expect.stringContaining("E1U1.mp3"), null]);
  });

  it("moves the playhead when the tracks are pressed with the primary button only", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)],
    });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);

    renderView();

    await screen.findByTestId("edit-clip-c1");
    const tracks = screen.getByRole("group", { name: "时间线轨道，点击跳到对应时间" });
    vi.spyOn(tracks, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 780, 100));
    const playhead = () => parseFloat(screen.getByTestId("edit-playhead").style.left);

    fireEvent.pointerDown(tracks, { button: 2, clientX: 390 });
    expect(playhead()).toBe(0);

    fireEvent.pointerDown(tracks, { button: 0, clientX: 390 });
    await waitFor(() => expect(playhead()).toBeCloseTo(50, 1));
  });

  describe("链接参数 tl / t", () => {
    const OTHER_CUT: EditTimelineReadout = {
      ...INITIAL_CUT,
      timeline: { id: "tl-00000001", name: "按脚本顺序", episode: 1 },
      duration: 4,
      clips: [clip({ id: "d1", unit_id: "E1U1", start: 0, duration: 2 }), clip({ id: "d2", unit_id: "E1U3", start: 2, duration: 2 })],
      issues: [],
    };

    function mockTimelines() {
      vi.spyOn(API, "listEditTimelines").mockResolvedValue({
        timelines: [
          summary("tl-00000001", "按脚本顺序", "2026-09-30T09:00:00Z"),
          summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3),
        ],
      });
      vi.spyOn(API, "getEditTimeline").mockImplementation((_project, id) =>
        Promise.resolve(id === "tl-00000001" ? OTHER_CUT : INITIAL_CUT),
      );
    }

    beforeEach(() => {
      useAppStore.setState({ toast: null });
    });

    afterEach(() => {
      window.history.replaceState(null, "", "/");
    });

    function playheadPercent(): number {
      return parseFloat(screen.getByTestId("edit-playhead").style.left);
    }

    it("打开链接指向的剪辑时间线，播放头移到该时间并选中落在其中的片段，地址栏的一次性参数随即去掉", async () => {
      mockTimelines();
      window.history.replaceState(null, "", "/?view=edit&tl=tl-00000001&t=3");

      renderView();

      // 默认会打开最近修改的「初剪」，链接要求的是另一条
      expect(await screen.findByRole("tab", { name: "按脚本顺序" })).toHaveAttribute("aria-selected", "true");
      await waitFor(() => expect(screen.getByTestId("edit-clip-d2")).toHaveAttribute("aria-pressed", "true"));
      expect(screen.getByTestId("edit-clip-d1")).toHaveAttribute("aria-pressed", "false");
      expect(playheadPercent()).toBeCloseTo(75, 1);
      expect(window.location.search).toBe("?view=edit");
    });

    it("只带时间点时在当前剪辑时间线上定位", async () => {
      mockTimelines();
      window.history.replaceState(null, "", "/?view=edit&t=5");

      renderView();

      await waitFor(() => expect(screen.getByTestId("edit-clip-c3")).toHaveAttribute("aria-pressed", "true"));
      expect(screen.getByRole("tab", { name: "初剪" })).toHaveAttribute("aria-selected", "true");
      expect(playheadPercent()).toBeCloseTo((5 / 7.8) * 100, 1);
    });

    it("链接指向的剪辑时间线已不存在时提示，并停在默认的那条", async () => {
      mockTimelines();
      window.history.replaceState(null, "", "/?view=edit&tl=tl-gone&t=3");

      renderView();

      expect(await screen.findByRole("tab", { name: "初剪" })).toHaveAttribute("aria-selected", "true");
      await waitFor(() => expect(useAppStore.getState().toast?.tone).toBe("warning"));
      expect(playheadPercent()).toBe(0);
    });

    it("时间点超出时长时夹到末尾，无法解析的时间点忽略", async () => {
      mockTimelines();
      window.history.replaceState(null, "", "/?view=edit&t=99");
      const { unmount } = renderView();
      await waitFor(() => expect(playheadPercent()).toBeCloseTo(100, 1));
      unmount();

      window.history.replaceState(null, "", "/?view=edit&t=abc");
      renderView();
      await screen.findByTestId("edit-clip-c1");
      expect(playheadPercent()).toBe(0);
    });

    it("链接指向刚新建、列表还没刷新到的剪辑时间线时，等新列表到了再定位，不误报不存在", async () => {
      const list = vi.spyOn(API, "listEditTimelines").mockResolvedValue({
        timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)],
      });
      vi.spyOn(API, "getEditTimeline").mockImplementation((_project, id) =>
        Promise.resolve(id === "tl-00000001" ? OTHER_CUT : INITIAL_CUT),
      );
      renderView();
      await screen.findByTestId("edit-clip-c1");

      let resolveList: (value: { timelines: EditTimelineSummary[] }) => void = () => {};
      list.mockReturnValue(new Promise((resolve) => (resolveList = resolve)));
      act(() => {
        useProjectsStore.setState({ projectSnapshotRevisions: { demo: 1 } });
        window.history.pushState(null, "", "/?view=edit&tl=tl-00000001&t=3");
        window.dispatchEvent(new PopStateEvent("popstate"));
      });
      act(() =>
        resolveList({
          timelines: [
            summary("tl-00000001", "按脚本顺序", "2026-09-30T09:00:00Z"),
            summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3),
          ],
        }),
      );

      await waitFor(() =>
        expect(screen.getByRole("tab", { name: "按脚本顺序" })).toHaveAttribute("aria-selected", "true"),
      );
      await waitFor(() => expect(screen.getByTestId("edit-clip-d2")).toHaveAttribute("aria-pressed", "true"));
      expect(useAppStore.getState().toast).toBeNull();
    });

    it("链接定位尚未完成时手动切换标签，之后列表刷新不会把标签切回链接那条", async () => {
      mockTimelines();
      const read = vi
        .spyOn(API, "getEditTimeline")
        .mockImplementation((_project, id) =>
          id === "tl-00000001" ? new Promise(() => {}) : Promise.resolve(INITIAL_CUT),
        );
      window.history.replaceState(null, "", "/?view=edit&tl=tl-00000001&t=3");
      renderView();
      await waitFor(() =>
        expect(screen.getByRole("tab", { name: "按脚本顺序" })).toHaveAttribute("aria-selected", "true"),
      );

      fireEvent.click(screen.getByRole("tab", { name: "初剪" }));
      await screen.findByTestId("edit-clip-c1");
      vi.mocked(API.listEditTimelines).mockResolvedValue({
        timelines: [
          summary("tl-00000001", "按脚本顺序", "2026-09-30T09:00:00Z"),
          summary("tl-00000002", "初剪 v2", "2026-09-30T10:05:00Z", 4),
        ],
      });
      act(() => useProjectsStore.setState({ projectSnapshotRevisions: { demo: 1 } }));

      await screen.findByRole("tab", { name: "初剪 v2" });
      await waitFor(() => expect(read).toHaveBeenLastCalledWith("demo", "tl-00000002", expect.anything()));
      expect(screen.getByRole("tab", { name: "初剪 v2" })).toHaveAttribute("aria-selected", "true");
      expect(playheadPercent()).toBe(0);
    });

    it("跳到别的项目时，参数留给切换后的那个项目的剪辑视图消费", async () => {
      mockTimelines();
      window.history.replaceState(null, "", "/app/projects/other/episodes/1?view=edit&tl=tl-00000001&t=3");
      const inRoute = (projectName: string) => (
        <Route path="/app/projects/:projectName" nest>
          <EditTimelineView
            key={projectName}
            projectName={projectName}
            episode={1}
            script={SCRIPT}
            aspect="16:9"
            ttsNarration
            renderEmptyState={NO_TIMELINE}
          />
        </Route>
      );
      const { rerender } = render(inRoute("demo"));
      await screen.findByTestId("edit-clip-c1");
      expect(window.location.search).toBe("?view=edit&tl=tl-00000001&t=3");

      rerender(inRoute("other"));
      await waitFor(() => expect(screen.getByTestId("edit-clip-d2")).toHaveAttribute("aria-pressed", "true"));
      expect(screen.getByRole("tab", { name: "按脚本顺序" })).toHaveAttribute("aria-selected", "true");
      expect(window.location.search).toBe("?view=edit");
    });

    it("已经在剪辑视图里时，再点同一条链接会重新定位", async () => {
      mockTimelines();
      renderView();
      await screen.findByTestId("edit-clip-c1");

      act(() => {
        window.history.pushState(null, "", "/?view=edit&tl=tl-00000002&t=6");
        window.dispatchEvent(new PopStateEvent("popstate"));
      });
      await waitFor(() => expect(playheadPercent()).toBeCloseTo((6 / 7.8) * 100, 1));

      act(() => {
        window.history.pushState(null, "", "/?view=edit&tl=tl-00000002&t=1");
        window.dispatchEvent(new PopStateEvent("popstate"));
      });
      await waitFor(() => expect(playheadPercent()).toBeCloseTo((1 / 7.8) * 100, 1));
      expect(screen.getByTestId("edit-clip-c1")).toHaveAttribute("aria-pressed", "true");
    });
  });
});
