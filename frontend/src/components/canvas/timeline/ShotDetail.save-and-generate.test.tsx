import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ShotDetail } from "./ShotDetail";
import type { NarrationSegment } from "@/types";
import { makeNarrationSegment } from "@/test/factories";

/** 有未保存修改时，生成按钮先保存再生成；保存没有完整生效时不生成。 */

function renderDetail(props: Partial<Parameters<typeof ShotDetail>[0]> = {}, segment: NarrationSegment = makeNarrationSegment()) {
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
      {...props}
    />,
  );
}

describe("ShotDetail 保存并生成", () => {
  it("有未保存修改时按钮写「保存并生成」，先提交修改再生成", async () => {
    const onUpdatePrompt = vi.fn().mockResolvedValue(true);
    const onGenerateStoryboard = vi.fn();
    renderDetail({ onUpdatePrompt, onGenerateStoryboard });

    expect(screen.queryByRole("button", { name: /保存并生成/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue("雨夜街道"), { target: { value: "雨后的街道" } });
    fireEvent.click(screen.getByRole("button", { name: /保存并生成/ }));

    await waitFor(() => expect(onGenerateStoryboard).toHaveBeenCalledWith("E1S01"));
    expect(onUpdatePrompt).toHaveBeenCalledWith("E1S01", { image_prompt: "雨后的街道" });
    expect(onUpdatePrompt.mock.invocationCallOrder[0]).toBeLessThan(onGenerateStoryboard.mock.invocationCallOrder[0]);
    // 保存成功后提示条收起，按钮回到普通文案
    await waitFor(() => expect(screen.queryByRole("button", { name: /保存并生成/ })).not.toBeInTheDocument());
  });

  it("保存失败时不生成，错误显示在提示条上", async () => {
    const onUpdatePrompt = vi.fn().mockRejectedValue(new Error("提示词不能为空"));
    const onGenerateStoryboard = vi.fn();
    renderDetail({ onUpdatePrompt, onGenerateStoryboard });

    fireEvent.change(screen.getByDisplayValue("雨夜街道"), { target: { value: "雨后的街道" } });
    fireEvent.click(screen.getByRole("button", { name: /保存并生成/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("提示词不能为空");
    expect(onGenerateStoryboard).not.toHaveBeenCalled();
    expect(screen.getByDisplayValue("雨后的街道")).toBeInTheDocument();
  });

  it("修改已写入但剧本没刷新时不生成，并说明需要刷新", async () => {
    const onUpdatePrompt = vi.fn().mockResolvedValue(false);
    const onGenerateNarration = vi.fn();
    renderDetail({ onUpdatePrompt, onGenerateNarration });

    fireEvent.change(screen.getByRole("textbox", { name: "旁白正文" }), { target: { value: "雨停了。" } });
    fireEvent.click(screen.getByRole("button", { name: /保存并生成/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("没能载入最新剧本");
    expect(onGenerateNarration).not.toHaveBeenCalled();
    // 再点也不能绕过刷新失败，直到真正的剧本内容到达。
    const generate = screen.getByRole("button", { name: /^生成旁白/ });
    expect(generate).toBeDisabled();
    fireEvent.click(generate);
    expect(onGenerateNarration).not.toHaveBeenCalled();
    // 已写入的内容成为已保存内容，不再标为未保存
    expect(screen.queryByRole("button", { name: "放弃修改" })).not.toBeInTheDocument();
  });

  it("视频「保存并生成」等保存落定期间，尾帧不能更换或清除，落定后再生成", async () => {
    let finishSave: (refreshed: boolean) => void = () => {};
    const onUpdatePrompt = vi.fn(() => new Promise<boolean>((resolve) => { finishSave = resolve; }));
    const onGenerateVideo = vi.fn();
    const segment = makeNarrationSegment({
      end_frame_image: "end_frames/E1S01.png",
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
    renderDetail({ onUpdatePrompt, onGenerateVideo, lastFrame: true }, segment);

    fireEvent.click(screen.getByRole("button", { name: /^尾帧/ }));
    fireEvent.change(screen.getByDisplayValue("雨夜街道"), { target: { value: "雨后的街道" } });
    fireEvent.click(screen.getByRole("button", { name: /保存并生成/ }));

    expect(screen.getByRole("button", { name: "更换图片" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "清除" })).toBeDisabled();
    expect(onGenerateVideo).not.toHaveBeenCalled();

    await act(async () => finishSave(true));
    await waitFor(() => expect(onGenerateVideo).toHaveBeenCalledWith("E1S01"));
  });

  it("没有未保存修改时直接生成，不提交修改", async () => {
    const onUpdatePrompt = vi.fn().mockResolvedValue(true);
    const onGenerateStoryboard = vi.fn();
    renderDetail({ onUpdatePrompt, onGenerateStoryboard });

    fireEvent.click(screen.getByRole("button", { name: /^生成分镜/ }));

    await waitFor(() => expect(onGenerateStoryboard).toHaveBeenCalledWith("E1S01"));
    expect(onUpdatePrompt).not.toHaveBeenCalled();
  });
});

describe("ShotDetail 备注与引用进入未保存修改", () => {
  it("备注与提示词一起保存", async () => {
    const onUpdatePrompt = vi.fn().mockResolvedValue(true);
    renderDetail({ onUpdatePrompt });

    fireEvent.click(screen.getByRole("button", { name: "备注" }));
    fireEvent.change(await screen.findByRole("textbox", { name: "备注" }), { target: { value: "换成雨后" } });
    fireEvent.change(screen.getByDisplayValue("雨夜街道"), { target: { value: "雨后的街道" } });
    expect(onUpdatePrompt).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(onUpdatePrompt).toHaveBeenCalledWith("E1S01", { image_prompt: "雨后的街道", note: "换成雨后" }),
    );
  });
});
