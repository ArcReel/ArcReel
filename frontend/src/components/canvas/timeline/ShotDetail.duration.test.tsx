import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ShotDetail } from "./ShotDetail";
import type { DramaScene } from "@/types";

/**
 * 逐镜头时长编辑器：候选取经联动约束收窄后的集合，已保存的越界值不静默改写、
 * 按成因给警告并引导重选。
 */

function makeScene(durationSeconds: number): DramaScene {
  return {
    scene_id: "E1S01",
    duration_seconds: durationSeconds,
    segment_break: false,
    characters_in_scene: [],
    scenes: [],
    props: [],
    image_prompt: {
      scene: "重逢",
      composition: { shot_type: "Medium Shot", lighting: "暖光", ambiance: "怀旧" },
    },
    video_prompt: { action: "推门而入", camera_motion: "Static", ambiance_audio: "", dialogue: [] },
    utterances: [],
    transition_to_next: "cut",
  };
}

function renderDetail(props: Partial<Parameters<typeof ShotDetail>[0]> = {}, seconds = 4) {
  return render(
    <ShotDetail
      segment={makeScene(seconds)}
      segmentId="E1S01"
      contentMode="drama"
      aspectRatio="9:16"
      projectName="demo"
      scriptFile="episode_1.json"
      selectedIndex={0}
      totalCount={1}
      onPrev={() => {}}
      onNext={() => {}}
      onUpdatePrompt={() => {}}
      durationOptions={[8]}
      {...props}
    />,
  );
}

/** 时长 pill 是唯一带秒数文案的按钮；越界时它带 aria-label 的 ⚠ 兄弟节点。 */
function warningLabel(): string | null {
  return screen.queryByText("⚠")?.getAttribute("aria-label") ?? null;
}

describe("ShotDetail 时长候选与越界提示", () => {
  it("只呈现收窄后的候选，越界的已保存值仍原样显示、不被改写", () => {
    const onUpdatePrompt = vi.fn();
    renderDetail({ onUpdatePrompt });

    // 存值 4 秒照常显示——静默改写会让用户在不知情下丢掉自己的设置
    expect(screen.getByRole("button", { name: /4 秒/ })).toBeInTheDocument();
    expect(onUpdatePrompt).not.toHaveBeenCalled();

    // 展开后只有收窄后的 8 秒可选
    fireEvent.click(screen.getByRole("button", { name: /4 秒/ }));
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(1);
    expect(radios[0]).toHaveTextContent("8 秒");
  });

  it("重选写回选中的候选值", () => {
    const onUpdatePrompt = vi.fn();
    renderDetail({ onUpdatePrompt });
    fireEvent.click(screen.getByRole("button", { name: /4 秒/ }));
    fireEvent.click(screen.getByRole("radio", { name: /8 秒/ }));
    expect(onUpdatePrompt).toHaveBeenCalledWith("E1S01", "duration_seconds", 8);
  });

  it("候选内的值不告警", () => {
    renderDetail({}, 8);
    expect(warningLabel()).toBeNull();
  });

  // 成因决定提示把用户引向哪里：分辨率 / 参考图两条改对应设置也能解决，
  // 说成「模型不支持」会把用户引去换模型。
  it("成因为分辨率时说清是分辨率，不说成模型不支持", () => {
    renderDetail({ durationWarningReason: () => "resolution" });
    expect(warningLabel()).toContain("当前分辨率");
  });

  it("成因为参考图路径时说清是该模式", () => {
    renderDetail({ durationWarningReason: () => "reference" });
    expect(warningLabel()).toContain("参考生视频");
  });

  it("成因为模型全集不含该值、或未传成因判定时用通用文案", () => {
    const { unmount } = renderDetail({ durationWarningReason: () => "model" });
    expect(warningLabel()).toContain("模型支持范围");
    unmount();

    // 未接线成因判定的调用点（如未来新增的画布）退回通用文案，而不是显示成 undefined key
    renderDetail();
    expect(warningLabel()).toContain("模型支持范围");
  });
});
