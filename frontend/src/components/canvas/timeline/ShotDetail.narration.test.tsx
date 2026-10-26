import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ShotDetail } from "./ShotDetail";
import { API } from "@/api";
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

const legacyDialogueSegment = (dialogue: { speaker: string; line: string }[]) =>
  makeNarrationSegment({
    video_prompt: { action: "撑伞走过", camera_motion: "Static", ambiance_audio: "雨声", dialogue },
  });

describe("ShotDetail 旁白片段的台词", () => {
  it("没有存量台词时不出现台词编辑入口", () => {
    render(detailElement(legacyDialogueSegment([]), { onUpdatePrompt: vi.fn() }));

    expect(screen.queryByText("对话")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "添加对话" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "清除台词" })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText("角色")).not.toBeInTheDocument();
  });

  it("存量台词只读列出，没有可编辑的输入框和添加入口", () => {
    render(detailElement(legacyDialogueSegment([{ speaker: "阿离", line: "你终于回来了。" }]), { onUpdatePrompt: vi.fn() }));

    expect(screen.getByText("阿离")).toBeInTheDocument();
    expect(screen.getByText("你终于回来了。")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("你终于回来了。")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "添加对话" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "清除台词" })).toBeEnabled();
  });

  it("清除台词并保存后，dialogue 提交为空，其余提示词字段保留", () => {
    const onUpdatePrompt = vi.fn();
    render(detailElement(legacyDialogueSegment([{ speaker: "阿离", line: "你终于回来了。" }]), { onUpdatePrompt }));

    fireEvent.click(screen.getByRole("button", { name: "清除台词" }));
    expect(screen.queryByText("你终于回来了。")).not.toBeInTheDocument();
    expect(screen.getByText("台词已清除，保存后生效")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(onUpdatePrompt).toHaveBeenCalledWith("E1S01", {
      video_prompt: { action: "撑伞走过", camera_motion: "Static", ambiance_audio: "雨声", dialogue: [] },
    });
  });

  it("清除台词后放弃修改，台词恢复显示", () => {
    render(detailElement(legacyDialogueSegment([{ speaker: "阿离", line: "你终于回来了。" }]), { onUpdatePrompt: vi.fn() }));

    fireEvent.click(screen.getByRole("button", { name: "清除台词" }));
    fireEvent.click(screen.getByRole("button", { name: "放弃修改" }));

    expect(screen.getByText("你终于回来了。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "清除台词" })).toBeInTheDocument();
  });

  it("视频提示词切到文本形态后，台词随渲染文本保留，不提示已清除", async () => {
    const rendered = "Action: 撑伞走过\nDialogue:\n- Speaker: 阿离\n  Line: 你终于回来了。";
    vi.spyOn(API, "previewScriptItemPrompts").mockResolvedValue({
      item_id: "E1S01",
      content_mode: "narration",
      storyboard_image: { text: "雨夜街道", unavailable: null, is_text_form: true, warnings: [] },
      video: { text: rendered, unavailable: null, is_text_form: false, warnings: [] },
    });
    render(detailElement(legacyDialogueSegment([{ speaker: "阿离", line: "你终于回来了。" }]), { onUpdatePrompt: vi.fn() }));

    const videoToText = screen.getAllByRole("button", { name: "文本" }).at(-1)!;
    fireEvent.click(videoToText);

    expect(await screen.findByDisplayValue(/Line: 你终于回来了。/)).toBeInTheDocument();
    expect(screen.queryByText("台词已清除，保存后生效")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "清除台词" })).not.toBeInTheDocument();
  });

  it("只读模式（缺 onUpdatePrompt）：台词可读，不提供清除", () => {
    render(detailElement(legacyDialogueSegment([{ speaker: "阿离", line: "你终于回来了。" }])));

    expect(screen.getByText("你终于回来了。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "清除台词" })).not.toBeInTheDocument();
  });
});

describe("ShotDetail 旁白正文", () => {
  it("编辑旁白正文后保存，只提交 { novel_text }", () => {
    const onUpdatePrompt = vi.fn();
    render(detailElement(makeNarrationSegment({ novel_text: "雨下了一夜。" }), { onUpdatePrompt }));

    fireEvent.change(screen.getByRole("textbox", { name: "旁白正文" }), { target: { value: "雨停了。" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(onUpdatePrompt).toHaveBeenCalledWith("E1S01", { novel_text: "雨停了。" });
  });

  it("上游保存后未修改的正文跟随新内容，放弃修改恢复为已保存内容", () => {
    const onUpdatePrompt = vi.fn();
    const { rerender } = render(detailElement(makeNarrationSegment({ novel_text: "雨下了一夜。" }), { onUpdatePrompt }));
    const box = () => screen.getByRole("textbox", { name: "旁白正文" }) as HTMLTextAreaElement;

    rerender(detailElement(makeNarrationSegment({ novel_text: "Agent 改过的正文。" }), { onUpdatePrompt }));
    expect(box().value).toBe("Agent 改过的正文。");

    fireEvent.change(box(), { target: { value: "改写中的正文" } });
    fireEvent.click(screen.getByRole("button", { name: "放弃修改" }));
    expect(box().value).toBe("Agent 改过的正文。");
  });

  it("只读模式（缺 onUpdatePrompt）：旁白正文不可编辑", () => {
    render(detailElement(makeNarrationSegment()));

    expect(screen.getByRole("textbox", { name: "旁白正文" })).toHaveAttribute("readonly");
  });
  it("新增旁白分镜先弹框填写正文，正文为空不可确认，确认后带正文新增", async () => {
    const onInsertShot = vi.fn().mockResolvedValue(true);
    render(detailElement(makeNarrationSegment(), { onUpdatePrompt: vi.fn(), onInsertShot }));

    fireEvent.click(screen.getByRole("button", { name: "在此后插入" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/旁白正文是该分镜的内容基底/)).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "新增分镜" });
    expect(confirm).toBeDisabled();

    fireEvent.change(within(dialog).getByRole("textbox", { name: "旁白正文" }), { target: { value: "风停了。" } });
    fireEvent.click(confirm);

    await waitFor(() => expect(onInsertShot).toHaveBeenCalledWith("E1S01", "风停了。"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
  it("本集唯一的分镜也可以移除，新增仍可用", () => {
    render(
      detailElement(makeNarrationSegment(), {
        onUpdatePrompt: vi.fn(),
        onInsertShot: vi.fn(),
        onRemoveShot: vi.fn(),
      })
    );

    expect(screen.getByRole("button", { name: "移除分镜" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "在此后插入" })).toBeEnabled();
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
    expect(remove).toHaveAccessibleDescription("该分镜有生成任务进行中，完成后才能移除");
    expect(screen.getByRole("button", { name: "在此后插入" })).toBeEnabled();
  });
});

describe("ShotDetail 改序与章节切分点", () => {
  it("旁白分镜在详情里前移、后移一位", () => {
    const onMoveShot = vi.fn();
    render(detailElement(makeNarrationSegment(), { onMoveShot, selectedIndex: 1, totalCount: 3 }));

    fireEvent.click(screen.getByRole("button", { name: "前移分镜" }));
    fireEvent.click(screen.getByRole("button", { name: "后移分镜" }));

    expect(onMoveShot.mock.calls).toEqual([
      ["E1S01", "earlier"],
      ["E1S01", "later"],
    ]);
  });

  it("切换章节切分点进入未保存修改，不弹确认，保存时写回", async () => {
    const onUpdatePrompt = vi.fn().mockResolvedValue(true);
    render(detailElement(makeNarrationSegment({ segment_break: false }), { onUpdatePrompt }));

    const toggle = screen.getByRole("switch", { name: "设为章节切分点" });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onUpdatePrompt).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(onUpdatePrompt).toHaveBeenCalledWith("E1S01", { segment_break: true }));
  });

  it("只读模式不给章节切分点开关", () => {
    render(detailElement(makeNarrationSegment()));

    expect(screen.queryByRole("switch", { name: "设为章节切分点" })).not.toBeInTheDocument();
  });
});
