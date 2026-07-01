import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ShotDetail } from "./ShotDetail";
import type { DramaScene, Utterance } from "@/types";

const sampleUtterances: Utterance[] = [
  { kind: "voiceover", speaker: null, text: "三年后。" },
  { kind: "dialogue", speaker: "阿离", text: "你终于回来了。" },
];

function makeScene(overrides: Partial<DramaScene> = {}): DramaScene {
  return {
    scene_id: "E1S01",
    duration_seconds: 8,
    segment_break: false,
    characters_in_scene: ["阿离"],
    scenes: [],
    props: [],
    image_prompt: {
      scene: "重逢",
      composition: { shot_type: "Medium Shot", lighting: "暖光", ambiance: "怀旧" },
    },
    video_prompt: { action: "推门而入", camera_motion: "Static", ambiance_audio: "风声", dialogue: [] },
    utterances: sampleUtterances,
    transition_to_next: "cut",
    ...overrides,
  };
}

function renderDetail(props: Partial<Parameters<typeof ShotDetail>[0]> = {}) {
  const scene = makeScene();
  return render(
    <ShotDetail
      segment={scene}
      segmentId={scene.scene_id}
      contentMode="drama"
      aspectRatio="9:16"
      projectName="demo"
      scriptFile="episode_1.json"
      selectedIndex={0}
      totalCount={3}
      onPrev={() => {}}
      onNext={() => {}}
      durationOptions={[8]}
      {...props}
    />,
  );
}

describe("ShotDetail drama 模式", () => {
  it("渲染 UtteranceListEditor：按时序展示画外音与带说话人的台词", () => {
    renderDetail();
    expect(screen.getByDisplayValue("三年后。")).toBeInTheDocument();
    expect(screen.getByDisplayValue("阿离")).toBeInTheDocument();
    expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument();
    // drama 不再渲染扁平对白编辑器的空态占位
    expect(screen.queryByText("（暂无对话）")).toBeNull();
  });

  it("编辑发声文本后保存，提交 { utterances } patch", () => {
    const onUpdatePrompt = vi.fn();
    renderDetail({ onUpdatePrompt });

    fireEvent.change(screen.getByDisplayValue("你终于回来了。"), {
      target: { value: "我回来了。" },
    });

    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(onUpdatePrompt).toHaveBeenCalledWith(
      "E1S01",
      expect.objectContaining({
        utterances: [
          { kind: "voiceover", speaker: null, text: "三年后。" },
          { kind: "dialogue", speaker: "阿离", text: "我回来了。" },
        ],
      }),
    );
  });

  it("新增画外音条目后保存，随 utterances 一并提交", () => {
    const onUpdatePrompt = vi.fn();
    renderDetail({ onUpdatePrompt });

    fireEvent.click(screen.getByText("添加画外音"));
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(onUpdatePrompt).toHaveBeenCalledWith(
      "E1S01",
      expect.objectContaining({
        utterances: [...sampleUtterances, { kind: "voiceover", speaker: null, text: "" }],
      }),
    );
  });

  it("上游静默更新时：干净草稿跟随新 utterances", () => {
    const { rerender } = renderDetail();

    const updated = makeScene({
      utterances: [{ kind: "dialogue", speaker: "阿离", text: "上游改写后的台词。" }],
    });
    rerender(
      <ShotDetail
        segment={updated}
        segmentId={updated.scene_id}
        contentMode="drama"
        aspectRatio="9:16"
        projectName="demo"
        scriptFile="episode_1.json"
        selectedIndex={0}
        totalCount={3}
        onPrev={() => {}}
        onNext={() => {}}
        durationOptions={[8]}
      />,
    );
    expect(screen.getByDisplayValue("上游改写后的台词。")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("你终于回来了。")).toBeNull();
  });
});
