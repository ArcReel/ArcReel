import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
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

describe("ShotDetail ad 模式", () => {
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

  it("展示镜头中的产品引用", () => {
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

  it("非 ad 模式不渲染移动按钮", () => {
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
