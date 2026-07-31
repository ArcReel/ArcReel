import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { UnitPreviewPanel } from "./UnitPreviewPanel";
import type { ReferenceVideoUnit } from "@/types";

// VersionTimeMachine 的 busy 只关面板内的恢复按钮，触发按钮的可用性不变；替身把这个
// 入参渲染成可断言的属性，避免为了读它去展开面板、加载版本列表。
vi.mock("@/components/canvas/timeline/VersionTimeMachine", () => ({
  VersionTimeMachine: ({
    busy,
    onRestoringChange,
  }: {
    busy?: boolean;
    onRestoringChange?: (restoring: boolean) => void;
  }) => (
    <div data-testid="version-time-machine" data-busy={String(Boolean(busy))}>
      {/* 替身把恢复态回传口暴露成一个按钮，供断言兄弟控件的反向互斥 */}
      <button type="button" data-testid="start-restore" onClick={() => onRestoringChange?.(true)} />
    </div>
  ),
}));

function versionMachineBusy(): boolean {
  return screen.getByTestId("version-time-machine").dataset.busy === "true";
}

function mkUnit(overrides: Partial<ReferenceVideoUnit> = {}): ReferenceVideoUnit {
  return {
    unit_id: "E1U1",
    shots: [{ duration: 3, text: "Shot 1 (3s): x" }],
    references: [],
    duration_seconds: 3,
    duration_override: false,
    transition_to_next: "cut",
    note: null,
    generated_assets: {
      storyboard_image: null,
      storyboard_last_image: null,
      grid_id: null,
      grid_cell_index: null,
      video_clip: null,
      video_uri: null,
      status: "pending",
      video_generated_at: null,
    },
    ...overrides,
  };
}

describe("UnitPreviewPanel", () => {
  it("shows placeholder when no unit is selected", () => {
    render(<UnitPreviewPanel unit={null} />);
    expect(screen.getByText(/Select a unit|选中左侧 Unit/)).toBeInTheDocument();
  });

  it("shows empty-video placeholder when unit has no video_clip", () => {
    render(<UnitPreviewPanel unit={mkUnit()} />);
    expect(screen.getByText(/Not yet generated|尚未生成/)).toBeInTheDocument();
  });

  it("renders <video> when video_clip is present", () => {
    const unit = mkUnit({
      generated_assets: {
        ...mkUnit().generated_assets,
        status: "completed",
        video_clip: "reference_videos/E1U1.mp4",
      },
    });
    const { container } = render(
      <UnitPreviewPanel unit={unit} projectName="proj" />,
    );
    expect(container.querySelector("video")).toBeInTheDocument();
  });

  it("invokes onUploadVideo with unit id and selected file", () => {
    const onUploadVideo = vi.fn();
    const { container } = render(
      <UnitPreviewPanel unit={mkUnit()} projectName="proj" onUploadVideo={onUploadVideo} />,
    );
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    const file = new File(["x"], "clip.mp4", { type: "video/mp4" });
    fireEvent.change(input!, { target: { files: [file] } });
    expect(onUploadVideo).toHaveBeenCalledWith("E1U1", file);
  });

  it("hides upload entry when onUploadVideo is not provided", () => {
    const { container } = render(<UnitPreviewPanel unit={mkUnit()} projectName="proj" />);
    expect(container.querySelector('input[type="file"]')).toBeNull();
  });

  it("disables upload button while the unit is generating", () => {
    const { container } = render(
      <UnitPreviewPanel
        unit={mkUnit()}
        projectName="proj"
        status="running"
        onUploadVideo={vi.fn()}
      />,
    );
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    const button = input?.nextElementSibling as HTMLButtonElement;
    expect(button).toBeDisabled();
  });

  // 版本恢复与生成回写同一个成片文件：占用期间恢复旧版本会显示成功、随后被在跑的
  // 生成任务覆盖。VersionTimeMachine 的 busy 关掉的是面板内的恢复按钮（触发按钮照常
  // 可开，只读浏览不受影响），故这里断言接线本身。
  describe("版本恢复的占用接线", () => {
    it("空闲时不置 busy", () => {
      render(<UnitPreviewPanel unit={mkUnit()} projectName="proj" />);
      expect(versionMachineBusy()).toBe(false);
    });

    it("生成中置 busy", () => {
      render(<UnitPreviewPanel unit={mkUnit()} projectName="proj" status="running" />);
      expect(versionMachineBusy()).toBe(true);
    });

    it("取消中置 busy——占用比 running 状态活得更久", () => {
      // cancelling 期间不展示为生成中（status 不是 running），但 worker 仍可能在写
      // 成片文件，占用判定仍成立；仅看 status 会漏禁用
      render(<UnitPreviewPanel unit={mkUnit()} projectName="proj" busy cancelling />);
      expect(versionMachineBusy()).toBe(true);
    });

    it("成片上传在途置 busy", () => {
      render(
        <UnitPreviewPanel unit={mkUnit()} projectName="proj" onUploadVideo={vi.fn()} uploadingVideo />,
      );
      expect(versionMachineBusy()).toBe(true);
    });

    it("恢复在途反向禁用同一 unit 的生成与上传", () => {
      // 恢复与生成、上传写同一个 reference_videos/{unit_id}.mp4：恢复返回前若这两个
      // 入口仍可点，两个请求并发落盘，后完成者覆盖前者且双方都提示成功。
      const { container } = render(
        <UnitPreviewPanel
          unit={mkUnit()}
          projectName="proj"
          onGenerate={vi.fn()}
          onUploadVideo={vi.fn()}
          restoring
        />,
      );
      const uploadButton = container.querySelector<HTMLInputElement>('input[type="file"]')
        ?.nextElementSibling as HTMLButtonElement;
      const generateButton = [...container.querySelectorAll("button")].find((b) =>
        b.textContent?.trim(),
      );

      expect(uploadButton).toBeDisabled();
      expect(generateButton).toBeDisabled();
    });

    it("外部恢复态同样置 busy——重挂载后不放行第二次恢复", () => {
      // 面板在窄屏 sub-tab / 宽屏右栏之间切换时会重挂载，VersionTimeMachine 自身的
      // 恢复中状态随之丢失；父级仍记录该 unit 恢复在途，此时放行会并发写同一成片文件。
      render(<UnitPreviewPanel unit={mkUnit()} projectName="proj" restoring />);
      expect(versionMachineBusy()).toBe(true);
    });

    it("恢复态上报带 unitId，供画布层按 unit 记录", () => {
      // 恢复态必须存在面板之外：本面板有窄屏 sub-tab / 宽屏右栏两处挂载点，切换会卸载
      // 它而在途请求不取消；且它随选中项复用，面板内的单个布尔量会串到别的 unit 上。
      const onRestoringChange = vi.fn();
      render(
        <UnitPreviewPanel
          unit={mkUnit({ unit_id: "E1U2" })}
          projectName="proj"
          onRestoringChange={onRestoringChange}
        />,
      );

      fireEvent.click(screen.getByTestId("start-restore"));

      expect(onRestoringChange).toHaveBeenCalledWith("E1U2", true);
    });
  });
});
