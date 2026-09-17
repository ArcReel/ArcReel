import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  it("新增旁白分镜先弹框填写正文，正文为空不可确认，确认后带正文新增", async () => {
    const onInsertShot = vi.fn().mockResolvedValue(true);
    render(detailElement(makeNarrationSegment(), { onUpdatePrompt: vi.fn(), onInsertShot }));

    fireEvent.click(screen.getByRole("button", { name: "新增分镜" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/旁白正文是该分镜的内容基底/)).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "新增分镜" });
    expect(confirm).toBeDisabled();

    fireEvent.change(within(dialog).getByRole("textbox", { name: "旁白正文" }), { target: { value: "风停了。" } });
    fireEvent.click(confirm);

    await waitFor(() => expect(onInsertShot).toHaveBeenCalledWith("E1S01", "风停了。"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
  it("旁白配音生成进行中时移除分镜禁用并说明原因，新增仍可用", () => {
    render(
      detailElement(makeNarrationSegment(), {
        onUpdatePrompt: vi.fn(),
        onInsertShot: vi.fn(),
        onRemoveShot: vi.fn(),
        generatingNarration: true,
      })
    );

    const remove = screen.getByRole("button", { name: "移除分镜" });
    expect(remove).toBeDisabled();
    expect(remove).toHaveAttribute("title", "该分镜有生成任务进行中，完成后才能移除");
    expect(screen.getByRole("button", { name: "新增分镜" })).toBeEnabled();
  });
});
