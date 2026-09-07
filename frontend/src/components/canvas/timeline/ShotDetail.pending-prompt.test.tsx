import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

describe("ShotDetail 待生成提示词", () => {
  it("两侧为 null 时各标「待生成」，文本框留空", () => {
    renderDetail(makeSegment());
    // 只看两个提示词区块：资产状态徽标另有一枚同文案的「待生成」
    for (const title of ["Image Prompt · 分镜图", "Video Prompt · 视频"]) {
      const section = screen.getByText(title).closest("section");
      expect(section).not.toBeNull();
      expect(within(section as HTMLElement).getByText("待生成")).toBeInTheDocument();
    }
    const textareas = screen.getAllByPlaceholderText(/描述/);
    expect(textareas.every((el) => (el as HTMLTextAreaElement).value === "")).toBe(true);
  });

  it("在待生成一侧输入再清空不算改动：保存按钮不可用、不发 PATCH", () => {
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

  it("失效条目显示提示并可「采用新内容」", async () => {
    const onAdoptPlanContent = vi.fn().mockResolvedValue(undefined);
    renderDetail(makeSegment({ image_prompt: "旧提示词", video_prompt: "旧动作" }), {
      promptsStale: true,
      onAdoptPlanContent,
    });
    expect(screen.getByText("内容已更新，提示词可能不符")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "采用新内容" }));
    await waitFor(() => expect(onAdoptPlanContent).toHaveBeenCalledTimes(1));
  });

  it("未失效条目不渲染失效提示", () => {
    renderDetail(makeSegment({ image_prompt: "提示词", video_prompt: "动作" }));
    expect(screen.queryByText("内容已更新，提示词可能不符")).not.toBeInTheDocument();
    for (const title of ["Image Prompt · 分镜图", "Video Prompt · 视频"]) {
      const section = screen.getByText(title).closest("section") as HTMLElement;
      expect(within(section).queryByText("待生成")).not.toBeInTheDocument();
    }
  });
});
