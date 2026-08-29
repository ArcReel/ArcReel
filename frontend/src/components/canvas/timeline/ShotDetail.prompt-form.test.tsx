import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ShotDetail } from "./ShotDetail";
import { API } from "@/api";
import type { ItemPromptPreview, NarrationSegment } from "@/types";

const RENDERED_IMAGE_PROMPT = "Style: Anime\n\nScene: 雨夜街道";

function makeSegment(overrides: Partial<NarrationSegment> = {}): NarrationSegment {
  return {
    segment_id: "E1S01",
    episode: 1,
    duration_seconds: 8,
    segment_break: false,
    novel_text: "旁白正文",
    characters_in_segment: [],
    scenes: [],
    props: [],
    image_prompt: {
      scene: "雨夜街道",
      composition: { shot_type: "Medium Shot", lighting: "暖光", ambiance: "薄雾" },
    },
    video_prompt: { action: "撑伞走过", camera_motion: "Static", ambiance_audio: "雨声", dialogue: [] },
    transition_to_next: "cut",
    ...overrides,
  };
}

function renderDetail(
  segment: NarrationSegment,
  props: Partial<Parameters<typeof ShotDetail>[0]> = {},
) {
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

function preview(): ItemPromptPreview {
  return {
    item_id: "E1S01",
    content_mode: "narration",
    storyboard_image: { text: RENDERED_IMAGE_PROMPT, unavailable: null, is_text_form: false },
    video: { text: "最终视频提示词", unavailable: null, is_text_form: false },
  };
}

describe("ShotDetail 提示词形态切换", () => {
  it("结构化 → 文本以后端渲染结果为初值，保存提交字符串形态", async () => {
    const spy = vi.spyOn(API, "previewScriptItemPrompts").mockResolvedValue(preview());
    const onUpdatePrompt = vi.fn();
    renderDetail(makeSegment(), { onUpdatePrompt });

    const [imageToText] = screen.getAllByRole("button", { name: "文本" });
    fireEvent.click(imageToText);

    await waitFor(() => expect(spy).toHaveBeenCalledWith("demo", "E1S01", "episode_1.json"));
    // 结构化编辑器让位给纯文本框，框里就是后端渲染的最终提示词
    expect(await screen.findByDisplayValue(/Scene: 雨夜街道/)).toBeInTheDocument();
    expect(screen.queryByDisplayValue("雨夜街道")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(onUpdatePrompt).toHaveBeenCalledWith(
      "E1S01",
      expect.objectContaining({ image_prompt: RENDERED_IMAGE_PROMPT }),
    );
  });

  it("文本 → 结构化须显式确认丢弃文本，确认后字段留空不做解析", () => {
    renderDetail(makeSegment({ image_prompt: "一段纯文本提示词" }));

    const [imageToStructured] = screen.getAllByRole("button", { name: "结构化" });
    fireEvent.click(imageToStructured);

    expect(screen.getByText("切换回结构化提示词？")).toBeInTheDocument();
    expect(screen.getByDisplayValue("一段纯文本提示词")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "丢弃并切换" }));

    expect(screen.queryByDisplayValue("一段纯文本提示词")).not.toBeInTheDocument();
    expect(screen.getByText("镜头")).toBeInTheDocument();
  });

  it("有未保存改动时禁用形态切换：初值取自已保存内容，切换会静默丢弃改动", () => {
    renderDetail(makeSegment());

    fireEvent.change(screen.getByDisplayValue("雨夜街道"), { target: { value: "改过的画面" } });

    for (const button of screen.getAllByRole("button", { name: "文本" })) {
      expect(button).toBeDisabled();
    }
  });
});
