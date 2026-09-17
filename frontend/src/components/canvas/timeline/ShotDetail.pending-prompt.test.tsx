import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ShotDetail } from "./ShotDetail";
import type { NarrationSegment } from "@/types";
import { makeNarrationSegment } from "@/test/factories";

function makeSegment(overrides: Partial<NarrationSegment> = {}): NarrationSegment {
  return makeNarrationSegment({ image_prompt: null, video_prompt: null, ...overrides });
}

function renderDetail(segment: NarrationSegment, props: Partial<Parameters<typeof ShotDetail>[0]> = {}) {
  return render(
    <ShotDetail
      segment={segment}
      segmentId={segment.segment_id}
      contentMode="narration"
      aspectRatio="9:16"
      projectName="demo"
      scriptFile="episode_1.json"
      selectedIndex={0}
      totalCount={1}
      onPrev={() => {}}
      onNext={() => {}}
      durationOptions={[8]}
      onUpdatePrompt={vi.fn()}
      {...props}
    />,
  );
}

describe("ShotDetail 空提示词", () => {
  it("两侧为 null 时不标「待生成」徽标，文本框留空", () => {
    renderDetail(makeSegment());
    // 只看两个提示词区块：资产状态徽标另有一枚同文案的「待生成」
    for (const title of ["Image Prompt · 分镜图", "Video Prompt · 视频"]) {
      const section = screen.getByText(title).closest("section");
      expect(section).not.toBeNull();
      expect(within(section as HTMLElement).queryByText("待生成")).not.toBeInTheDocument();
    }
    const textareas = screen.getAllByPlaceholderText(/描述/);
    expect(textareas.every((el) => (el as HTMLTextAreaElement).value === "")).toBe(true);
  });

  it("在空提示词一侧输入再清空不算改动：保存按钮不可用、不发 PATCH", () => {
    const onUpdatePrompt = vi.fn();
    renderDetail(makeSegment(), { onUpdatePrompt });
    const [imageBox] = screen.getAllByPlaceholderText(/描述/);
    fireEvent.change(imageBox, { target: { value: "雨夜" } });
    expect(screen.getByRole("button", { name: "保存" })).toBeEnabled();
    fireEvent.change(imageBox, { target: { value: "" } });
    // 草稿回到干净态：保存入口收起，没有任何 PATCH 发出
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
    expect(onUpdatePrompt).not.toHaveBeenCalled();
  });

  it("填写后保存只提交填写的那一侧", () => {
    const onUpdatePrompt = vi.fn();
    renderDetail(makeSegment(), { onUpdatePrompt });
    const [imageBox] = screen.getAllByPlaceholderText(/描述/);
    fireEvent.change(imageBox, { target: { value: "雨夜街道" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(onUpdatePrompt).toHaveBeenCalledWith("E1S01", { image_prompt: "雨夜街道" });
  });

  it("待编写条目显示待编写提示", () => {
    renderDetail(makeSegment({ image_prompt: "提示词", video_prompt: "动作", pending_authoring: true }));
    expect(screen.getByText(/^待编写/)).toBeInTheDocument();
  });

  it("未带待编写标记的条目即使提示词为空也不显示待编写提示", () => {
    renderDetail(makeSegment());
    expect(screen.queryByText(/^待编写/)).not.toBeInTheDocument();
  });
});
