import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ShotDetail } from "./ShotDetail";
import type { NarrationSegment } from "@/types";
import { makeNarrationSegment } from "@/test/factories";

function detailElement(segment: NarrationSegment, props: Partial<Parameters<typeof ShotDetail>[0]> = {}) {
  return (
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
      {...props}
    />
  );
}

describe("ShotDetail 旁白正文", () => {
  it("编辑旁白正文后保存，只提交 { novel_text }", () => {
    const onUpdatePrompt = vi.fn();
    render(detailElement(makeNarrationSegment({ novel_text: "雨下了一夜。" }), { onUpdatePrompt }));

    fireEvent.change(screen.getByRole("textbox", { name: "旁白正文" }), { target: { value: "雨停了。" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(onUpdatePrompt).toHaveBeenCalledWith("E1S01", { novel_text: "雨停了。" });
  });

  it("上游保存后草稿跟随新正文，取消恢复为已保存内容", () => {
    const onUpdatePrompt = vi.fn();
    const { rerender } = render(detailElement(makeNarrationSegment({ novel_text: "雨下了一夜。" }), { onUpdatePrompt }));
    const box = () => screen.getByRole("textbox", { name: "旁白正文" }) as HTMLTextAreaElement;

    rerender(detailElement(makeNarrationSegment({ novel_text: "Agent 改过的正文。" }), { onUpdatePrompt }));
    expect(box().value).toBe("Agent 改过的正文。");

    fireEvent.change(box(), { target: { value: "草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(box().value).toBe("Agent 改过的正文。");
  });

  it("只读模式（缺 onUpdatePrompt）：旁白正文不可编辑", () => {
    render(detailElement(makeNarrationSegment()));

    expect(screen.getByRole("textbox", { name: "旁白正文" })).toHaveAttribute("readonly");
  });
});
