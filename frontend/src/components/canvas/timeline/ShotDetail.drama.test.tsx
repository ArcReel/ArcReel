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

// 统一构造 ShotDetail 元素，供首渲染与 rerender 共用，避免重复整段 props 列表。
function detailElement(scene: DramaScene, props: Partial<Parameters<typeof ShotDetail>[0]> = {}) {
  return (
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
    />
  );
}

function renderDetail(props: Partial<Parameters<typeof ShotDetail>[0]> = {}) {
  return render(detailElement(makeScene(), props));
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
    rerender(detailElement(updated));
    expect(screen.getByDisplayValue("上游改写后的台词。")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("你终于回来了。")).toBeNull();
  });

  it("上游画外音缺省 speaker 时：类型往返切换不产生虚假脏态（归一化签名）", () => {
    // 存量画外音只有 kind/text、缺 speaker 键（类型允许 speaker?: null）
    const scene = makeScene({ utterances: [{ kind: "voiceover", text: "三年后。" }] });
    render(detailElement(scene, { onUpdatePrompt: vi.fn() }));
    // 初始干净：保存栏不渲染
    expect(screen.queryByRole("button", { name: "保存" })).toBeNull();

    const toggleTitle = "在台词与画外音间切换";
    // 切到台词：真实变更 → 变脏 → 保存栏出现
    fireEvent.click(screen.getByTitle(toggleTitle));
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeNull();
    // 切回画外音（speaker 归 null，文本不变）：归一化后与上游等价 → 复归干净 → 保存栏消失
    fireEvent.click(screen.getByTitle(toggleTitle));
    expect(screen.queryByRole("button", { name: "保存" })).toBeNull();
  });

  it("只读模式（缺 onUpdatePrompt）：发声编辑器禁用，无法进入脏态", () => {
    renderDetail();
    expect(screen.getByText("添加画外音").closest("button")).toBeDisabled();
    expect(screen.getByText("添加台词").closest("button")).toBeDisabled();
    expect(screen.getByDisplayValue("你终于回来了。")).toBeDisabled();
  });
});
