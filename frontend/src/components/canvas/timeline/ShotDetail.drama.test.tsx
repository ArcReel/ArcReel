import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

describe("ShotDetail 剧情演绎", () => {
  it("渲染 UtteranceListEditor：按时序展示画外音与带说话人的台词", () => {
    renderDetail();
    expect(screen.getByDisplayValue("三年后。")).toBeInTheDocument();
    expect(screen.getByDisplayValue("阿离")).toBeInTheDocument();
    expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument();
    // drama 不再渲染扁平对白编辑器的空态占位
    expect(screen.queryByText("（暂无对话）")).not.toBeInTheDocument();
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
    expect(screen.queryByDisplayValue("你终于回来了。")).not.toBeInTheDocument();
  });

  it("上游画外音缺省 speaker 时：类型往返切换不产生虚假脏态（归一化签名）", () => {
    // 存量画外音只有 kind/text、缺 speaker 键（类型允许 speaker?: null）
    const scene = makeScene({ utterances: [{ kind: "voiceover", text: "三年后。" }] });
    render(detailElement(scene, { onUpdatePrompt: vi.fn() }));
    // 初始干净：保存栏不渲染
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();

    const toggleTitle = "在台词与画外音间切换";
    // 切到台词：真实变更 → 变脏 → 保存栏出现
    fireEvent.click(screen.getByTitle(toggleTitle));
    expect(screen.getByRole("button", { name: "保存" })).toBeInTheDocument();
    // 切回画外音（speaker 归 null，文本不变）：归一化后与上游等价 → 复归干净 → 保存栏消失
    fireEvent.click(screen.getByTitle(toggleTitle));
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
  });

  it("只读模式（缺 onUpdatePrompt）：发声编辑器禁用，无法进入脏态", () => {
    renderDetail();
    expect(screen.getByText("添加画外音").closest("button")).toBeDisabled();
    expect(screen.getByText("添加台词").closest("button")).toBeDisabled();
    expect(screen.getByDisplayValue("你终于回来了。")).toBeDisabled();
  });

  it("移除画外音后仍显示已付费旁白的只读历史", () => {
    const scene = makeScene({
      utterances: [{ kind: "dialogue", speaker: "阿离", text: "你终于回来了。" }],
      generated_assets: {
        storyboard_image: null,
        storyboard_last_image: null,
        grid_id: null,
        grid_cell_index: null,
        video_clip: null,
        video_thumbnail: null,
        video_uri: null,
        narration_audio: "audio/segment_E1S01.wav",
        status: "completed",
      },
    });

    const { container } = render(detailElement(scene, { onGenerateNarration: vi.fn() }));

    expect(container.querySelector('audio[src*="audio/segment_E1S01.wav"]')).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重新生成旁白配音|Regenerate narration audio/ })).toBeDisabled();
  });

  it("对应原文只读展示：可编辑模式下也没有编辑控件", () => {
    render(detailElement(makeScene({ source_text: "三年后，阿离推门而入。" }), { onUpdatePrompt: vi.fn() }));

    const region = screen.getByRole("region", { name: "对应原文" });
    expect(within(region).getByText("三年后，阿离推门而入。")).toBeInTheDocument();
    expect(within(region).queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("对应原文按逐字原样展示，不裁掉首尾空白", () => {
    render(detailElement(makeScene({ source_text: "  阿离推门而入。\n" })));

    const region = screen.getByRole("region", { name: "对应原文" });
    expect(within(region).getByText("阿离推门而入。")).toHaveTextContent("  阿离推门而入。\n", {
      normalizeWhitespace: false,
    });
  });

  it.each([undefined, "  \n"])("没有对应原文时显示空态（%j）", (sourceText) => {
    render(detailElement(makeScene({ source_text: sourceText })));

    const region = screen.getByRole("region", { name: "对应原文" });
    expect(within(region).getByText("（无对应原文）")).toBeInTheDocument();
  });
  describe("新增 / 移除分镜", () => {
    it("新增分镜直接在当前分镜之后插入空分镜，不弹框", async () => {
      const onInsertShot = vi.fn().mockResolvedValue(true);
      renderDetail({ onUpdatePrompt: vi.fn(), onInsertShot, onRemoveShot: vi.fn() });

      fireEvent.click(screen.getByRole("button", { name: "新增分镜" }));

      await waitFor(() => expect(onInsertShot).toHaveBeenCalledWith("E1S01"));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("移除分镜先弹 danger 确认框说明产物去向，确认后才移除", async () => {
      const onRemoveShot = vi.fn().mockResolvedValue(true);
      renderDetail({ onUpdatePrompt: vi.fn(), onInsertShot: vi.fn(), onRemoveShot });

      fireEvent.click(screen.getByRole("button", { name: "移除分镜" }));
      const dialog = screen.getByRole("dialog");
      expect(within(dialog).getByText("移除分镜 E1S01？")).toBeInTheDocument();
      expect(within(dialog).getByText(/产物随分镜一并移除/)).toBeInTheDocument();
      expect(onRemoveShot).not.toHaveBeenCalled();

      fireEvent.click(within(dialog).getByRole("button", { name: "移除分镜" }));

      await waitFor(() => expect(onRemoveShot).toHaveBeenCalledWith("E1S01"));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });

    it("有未保存草稿时增删入口禁用；未传回调时不渲染入口", () => {
      const { unmount } = renderDetail({ onUpdatePrompt: vi.fn(), onInsertShot: vi.fn(), onRemoveShot: vi.fn() });
      fireEvent.change(screen.getByDisplayValue("三年后。"), { target: { value: "五年后。" } });
      expect(screen.getByRole("button", { name: "新增分镜" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "移除分镜" })).toBeDisabled();
      unmount();

      renderDetail({ onUpdatePrompt: vi.fn() });
      expect(screen.queryByRole("button", { name: "新增分镜" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "移除分镜" })).not.toBeInTheDocument();
    });
  });
});
