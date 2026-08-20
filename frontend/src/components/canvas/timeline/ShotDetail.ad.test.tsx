import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NarratedVideoDurationError } from "@/api";
import { ShotDetail } from "./ShotDetail";
import { useCostStore } from "@/stores/cost-store";
import type { AdShot } from "@/types";

function makeShot(overrides: Partial<AdShot> = {}): AdShot {
  return {
    shot_id: "E1S01",
    section: "hook",
    duration_seconds: 4,
    voiceover_text: "还在等杯子干？",
    characters_in_shot: [],
    scenes: [],
    props: [],
    products_in_shot: ["速干杯"],
    image_prompt: {
      scene: "速干杯特写",
      composition: { shot_type: "Close-up", lighting: "顶光", ambiance: "清爽" },
    },
    video_prompt: {
      action: "水珠滑落",
      camera_motion: "Static",
      ambiance_audio: "水声",
      dialogue: [],
    },
    transition_to_next: "cut",
    ...overrides,
  };
}

function renderDetail(props: Partial<Parameters<typeof ShotDetail>[0]> = {}) {
  const shot = makeShot();
  return render(
    <ShotDetail
      segment={shot}
      segmentId={shot.shot_id}
      contentMode="ad"
      aspectRatio="9:16"
      projectName="demo"
      scriptFile="episode_1.json"
      selectedIndex={0}
      totalCount={3}
      onPrev={() => {}}
      onNext={() => {}}
      durationOptions={[4, 6, 8]}
      {...props}
    />,
  );
}

describe("ShotDetail 广告/短片", () => {
  it("展示口播文案与 section，可编辑并随保存提交 patch", () => {
    const onUpdatePrompt = vi.fn();
    renderDetail({ onUpdatePrompt });

    const voiceover = screen.getByDisplayValue("还在等杯子干？");
    fireEvent.change(voiceover, { target: { value: "三秒速干，告别水渍" } });

    const section = screen.getByDisplayValue("hook");
    fireEvent.change(section, { target: { value: "demo" } });

    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(onUpdatePrompt).toHaveBeenCalledWith(
      "E1S01",
      expect.objectContaining({ voiceover_text: "三秒速干，告别水渍", section: "demo" }),
    );
  });

  it("展示镜头中的商品引用", () => {
    renderDetail();
    expect(screen.getByText("速干杯")).toBeInTheDocument();
  });

  it("前移/后移按钮调用 onMoveShot", () => {
    const onMoveShot = vi.fn();
    renderDetail({ onMoveShot, selectedIndex: 1 });

    fireEvent.click(screen.getByRole("button", { name: "前移镜头" }));
    expect(onMoveShot).toHaveBeenCalledWith("E1S01", "earlier");

    fireEvent.click(screen.getByRole("button", { name: "后移镜头" }));
    expect(onMoveShot).toHaveBeenCalledWith("E1S01", "later");
  });

  it("首镜头禁用前移、末镜头禁用后移", () => {
    const onMoveShot = vi.fn();
    const first = renderDetail({ onMoveShot, selectedIndex: 0 });
    expect(screen.getByRole("button", { name: "前移镜头" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "后移镜头" })).toBeEnabled();
    first.unmount();

    renderDetail({ onMoveShot, selectedIndex: 2 });
    expect(screen.getByRole("button", { name: "前移镜头" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "后移镜头" })).toBeDisabled();
  });

  it("上游静默更新时：干净草稿跟随新值，脏草稿保留用户输入", () => {
    const shot = makeShot();
    const { rerender } = renderDetail({ segment: shot });

    // 干净草稿：上游 voiceover 变更后输入框跟随新值
    const updated = makeShot({ voiceover_text: "上游改写后的口播" });
    rerender(
      <ShotDetail
        segment={updated}
        segmentId={updated.shot_id}
        contentMode="ad"
        aspectRatio="9:16"
        projectName="demo"
        scriptFile="episode_1.json"
        selectedIndex={0}
        totalCount={3}
        onPrev={() => {}}
        onNext={() => {}}
        durationOptions={[4, 6, 8]}
      />,
    );
    expect(screen.getByDisplayValue("上游改写后的口播")).toBeInTheDocument();

    // 脏草稿：用户先编辑，再有上游变更，保留用户输入
    fireEvent.change(screen.getByDisplayValue("上游改写后的口播"), {
      target: { value: "用户手改的口播" },
    });
    const updatedAgain = makeShot({ voiceover_text: "第二次上游改写" });
    rerender(
      <ShotDetail
        segment={updatedAgain}
        segmentId={updatedAgain.shot_id}
        contentMode="ad"
        aspectRatio="9:16"
        projectName="demo"
        scriptFile="episode_1.json"
        selectedIndex={0}
        totalCount={3}
        onPrev={() => {}}
        onNext={() => {}}
        durationOptions={[4, 6, 8]}
      />,
    );
    expect(screen.getByDisplayValue("用户手改的口播")).toBeInTheDocument();
  });

  it("镜头级费用预估展示在生成按钮上", () => {
    useCostStore.setState({
      _segmentIndex: new Map([
        [
          "E1S01",
          {
            segment_id: "E1S01",
            duration_seconds: 4,
            estimate: { image: { USD: 0.067 }, video: { USD: 0.32 }, audio: {} },
            actual: { image: {}, video: {}, audio: {} },
          },
        ],
      ]),
    });
    const view = renderDetail({ onGenerateStoryboard: vi.fn(), onGenerateVideo: vi.fn() });
    try {
      expect(screen.getByText("~$0.07")).toBeInTheDocument();
      expect(screen.getByText("~$0.32")).toBeInTheDocument();
    } finally {
      // 先卸载再重置 store：组件仍挂载时清 store 会触发 act() 外的重渲染告警
      view.unmount();
      useCostStore.getState().clear();
    }
  });

  it("未接生成回调（参考生视频路径）时不渲染尾帧设置行", () => {
    renderDetail();
    expect(screen.queryByText("尾帧")).toBeNull();
  });

  it("接了 onGenerateVideo 时渲染尾帧设置行", () => {
    renderDetail({ onGenerateVideo: vi.fn() });
    expect(screen.getByText("尾帧")).toBeInTheDocument();
  });

  it("TTS 视频跨档时先确认，并仅用服务端返回的精确档位重试", async () => {
    const onGenerateVideo = vi
      .fn()
      .mockRejectedValueOnce(new NarratedVideoDurationError({
        allowed: false,
        kind: "narrated_video_duration",
        unit_id: "E1S01",
        narration_delivery: {},
        planned_duration: 4,
        duration_input: 6.2,
        request_duration: 8,
        adjustment: "up",
        problems: [{
          code: "reference_duration_confirmation_required",
          blocking: true,
          unit_id: "E1S01",
          locations: [{ path: ["duration_seconds"], line: null }],
          params: { duration_input: 6.2, request_duration: 8 },
          reason: "request_duration_uses_different_tier",
          action: "confirm_duration",
          message: "本次时长基准 6.2s 将按 8s 档位生成，请确认后重试",
        }],
      }))
      .mockResolvedValueOnce(undefined);
    renderDetail({
      segment: makeShot({
        generated_assets: {
          storyboard_image: "storyboards/E1S01.png",
          storyboard_last_image: null,
          grid_id: null,
          grid_cell_index: null,
          video_clip: null,
          video_thumbnail: null,
          video_uri: null,
          status: "storyboard_ready",
        },
      }),
      onGenerateVideo,
    });

    fireEvent.click(screen.getByRole("button", { name: "使用当前 TTS" }));
    fireEvent.click(screen.getByRole("button", { name: "生成视频" }));

    await waitFor(() => {
      expect(onGenerateVideo).toHaveBeenNthCalledWith(1, "E1S01", {
        narration_delivery: "use_tts",
      });
    });
    expect(screen.getByText(/6\.2 秒|6\.2s/)).toBeInTheDocument();
    expect(screen.getByText("8 秒")).toBeInTheDocument();
    expect(screen.getByText(/视频费用按申请的 8 秒\s*档位计算/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "按此时长生成" }));
    await waitFor(() => {
      expect(onGenerateVideo).toHaveBeenNthCalledWith(2, "E1S01", {
        narration_delivery: "use_tts",
        confirmed_request_duration_seconds: 8,
      });
    });
  });

  it("后期配音是请求默认值，生成视频不会暗中触发 TTS", () => {
    const onGenerateVideo = vi.fn();
    const onGenerateNarration = vi.fn();
    renderDetail({
      segment: makeShot({
        generated_assets: {
          storyboard_image: "storyboards/E1S01.png",
          storyboard_last_image: null,
          grid_id: null,
          grid_cell_index: null,
          video_clip: null,
          video_thumbnail: null,
          video_uri: null,
          status: "storyboard_ready",
        },
      }),
      onGenerateVideo,
      onGenerateNarration,
    });

    fireEvent.click(screen.getByRole("button", { name: "生成视频" }));

    expect(onGenerateVideo).toHaveBeenCalledWith("E1S01", {
      narration_delivery: "post_production",
    });
    expect(onGenerateNarration).not.toHaveBeenCalled();
  });

  it("同一镜头清空口播后隐藏的 TTS 选择会重置为后期配音", () => {
    const onGenerateVideo = vi.fn();
    const withNarration = makeShot({
      generated_assets: {
        storyboard_image: "storyboards/E1S01.png",
        storyboard_last_image: null,
        grid_id: null,
        grid_cell_index: null,
        video_clip: null,
        video_thumbnail: null,
        video_uri: null,
        status: "storyboard_ready",
      },
    });
    const { rerender } = renderDetail({ segment: withNarration, onGenerateVideo });

    fireEvent.click(screen.getByRole("button", { name: "使用当前 TTS" }));
    rerender(
      <ShotDetail
        segment={makeShot({ ...withNarration, voiceover_text: "" })}
        segmentId="E1S01"
        contentMode="ad"
        aspectRatio="9:16"
        projectName="demo"
        scriptFile="episode_1.json"
        selectedIndex={0}
        totalCount={3}
        onPrev={() => {}}
        onNext={() => {}}
        durationOptions={[4, 6, 8]}
        onGenerateVideo={onGenerateVideo}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "生成视频" }));

    expect(onGenerateVideo).toHaveBeenCalledWith("E1S01", {
      narration_delivery: "post_production",
    });
  });

  it("重排请求在途时移动按钮禁用（movePending）", () => {
    const onMoveShot = vi.fn();
    renderDetail({ onMoveShot, movePending: true, selectedIndex: 1 });
    expect(screen.getByRole("button", { name: "前移镜头" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "后移镜头" })).toBeDisabled();
    // 切镜导航同样锁定：完成回调按当前索引偏移，在途切镜会让选中态跳到错误镜头
    expect(screen.getByRole("button", { name: "上一镜" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "下一镜" })).toBeDisabled();
    // tooltip 解释禁用原因，而非展示常规操作提示
    expect(screen.getByRole("button", { name: "上一镜" })).toHaveAttribute("title", "重排进行中…");
    expect(screen.getByRole("button", { name: "前移镜头" })).toHaveAttribute("title", "重排进行中…");
  });

  it("非 广告/短片不渲染移动按钮", () => {
    const seg = {
      segment_id: "E1S01",
      episode: 1,
      duration_seconds: 4,
      segment_break: false,
      novel_text: "原文",
      characters_in_segment: [],
      image_prompt: "img",
      video_prompt: "vid",
      transition_to_next: "cut" as const,
    };
    render(
      <ShotDetail
        segment={seg}
        segmentId="E1S01"
        contentMode="narration"
        aspectRatio="9:16"
        projectName="demo"
        selectedIndex={0}
        totalCount={1}
        onPrev={() => {}}
        onNext={() => {}}
        onMoveShot={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "前移镜头" })).toBeNull();
  });
});
