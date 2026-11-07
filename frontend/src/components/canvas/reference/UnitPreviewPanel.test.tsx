import { describe, it, expect, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { useAppStore } from "@/stores/app-store";
import { UnitPreviewPanel } from "./UnitPreviewPanel";
import type { ReferenceVideoUnit, UnitGeneratedAssets } from "@/types";

// VersionTimeMachine busy disables inner restore buttons, not triggers; doubles render this
// prop as assertable attributes, avoiding opening panels/loading versions just to inspect it.
vi.mock("@/components/canvas/timeline/VersionTimeMachine", () => ({
  VersionTimeMachine: ({
    busy,
    onRestoringChange,
  }: {
    busy?: boolean;
    onRestoringChange?: (restoring: boolean) => void;
  }) => (
    <div data-testid="version-time-machine" data-busy={String(Boolean(busy))}>
      {/* Double exposes restoration-state reporting as a button to verify reverse sibling mutual exclusion. */}
      <button type="button" data-testid="start-restore" onClick={() => onRestoringChange?.(true)} />
    </div>
  ),
}));

vi.mock("@/components/shared/PresentationPlayer", () => ({
  PresentationPlayer: ({
    resourceId,
    startAt,
    posterPath,
  }: {
    resourceId: string;
    startAt?: { seconds: number };
    posterPath?: string | null;
  }) => (
    <div
      data-testid="presentation-player"
      data-resource-id={resourceId}
      data-start-at={startAt?.seconds}
      data-poster-path={posterPath ?? undefined}
    />
  ),
}));

function versionMachineBusy(): boolean {
  return screen.getByTestId("version-time-machine").dataset.busy === "true";
}

function mkUnit(
  overrides: Partial<ReferenceVideoUnit> = {},
): ReferenceVideoUnit & { generated_assets: UnitGeneratedAssets } {
  return {
    unit_id: "E1U1",
    text: "x",
    duration_seconds: 3,
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
    expect(screen.getByText(/Select a unit|选中左侧的视频单元/)).toBeInTheDocument();
  });

  it("shows empty-video placeholder when unit has no video_clip", () => {
    render(<UnitPreviewPanel unit={mkUnit()} />);
    expect(screen.getByText(/Not yet generated|尚未生成/)).toBeInTheDocument();
  });

  it("renders the shared presentation when video_clip is present", () => {
    const unit = mkUnit({
      generated_assets: {
        ...mkUnit().generated_assets,
        status: "completed",
        video_clip: "reference_videos/E1U1.mp4",
      },
    });
    render(<UnitPreviewPanel unit={unit} projectName="proj" />);
    expect(screen.getByTestId("presentation-player")).toHaveAttribute("data-resource-id", "E1U1");
    expect(screen.getByTestId("presentation-player")).not.toHaveAttribute("data-poster-path");
  });

  it("uses the extracted thumbnail as the player poster", () => {
    const unit = mkUnit({
      generated_assets: {
        ...mkUnit().generated_assets,
        status: "completed",
        video_clip: "reference_videos/E1U1.mp4",
        video_thumbnail: "reference_videos/thumbnails/E1U1.jpg",
      },
    });
    render(<UnitPreviewPanel unit={unit} projectName="proj" />);
    expect(screen.getByTestId("presentation-player")).toHaveAttribute(
      "data-poster-path",
      "reference_videos/thumbnails/E1U1.jpg",
    );
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
    render(
      <UnitPreviewPanel
        unit={mkUnit()}
        projectName="proj"
        status="running"
        onUploadVideo={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "上传视频" })).toBeDisabled();
  });

  it("keeps retained narration audio visible after the unit loses narration", () => {
    const unit = mkUnit({
      generated_assets: {
        ...mkUnit().generated_assets,
        narration_audio: "audio/segment_E1U1.wav",
      },
    });

    const { container } = render(
      <UnitPreviewPanel
        unit={unit}
        projectName="proj"
        narrationText=""
        onGenerateNarration={vi.fn()}
      />,
    );

    expect(container.querySelector('audio[src*="audio/segment_E1U1.wav"]')).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Regenerate narration audio|重新生成旁白配音/ })).toBeDisabled();
  });

  // Restore/generation write the same final file: occupied restoration could show success before running
  // generation overwrites. VersionTimeMachine busy disables inner restore, not opening triggers
  // because read-only browsing remains available; assert wiring itself here.
  describe("版本恢复的占用接线", () => {
    it("空闲时不置 busy", () => {
      render(<UnitPreviewPanel unit={mkUnit()} projectName="proj" />);
      expect(versionMachineBusy()).toBe(false);
    });

    it("生成中置 busy", () => {
      render(<UnitPreviewPanel unit={mkUnit()} projectName="proj" status="running" />);
      expect(versionMachineBusy()).toBe(true);
    });

    it("占用命中而 status 仍是旧失败行时置 busy", () => {
      // Retry optimistic windows retain failed rows and nonrunning status; status-only checks miss disabling.
      render(<UnitPreviewPanel unit={mkUnit()} projectName="proj" status="failed" busy />);
      expect(versionMachineBusy()).toBe(true);
    });

    it("成片上传在途置 busy", () => {
      render(
        <UnitPreviewPanel unit={mkUnit()} projectName="proj" onUploadVideo={vi.fn()} uploadingVideo />,
      );
      expect(versionMachineBusy()).toBe(true);
    });

    it("恢复在途反向禁用同一 unit 的生成与上传", () => {
      // Restore/generate/upload write reference_videos/{unit_id}.mp4. If generation/upload remain enabled
      // before restoration returns, concurrent writes overwrite by settlement order while both report success.
      const { container } = render(
        <UnitPreviewPanel
          unit={mkUnit()}
          projectName="proj"
          onGenerate={vi.fn()}
          onUploadVideo={vi.fn()}
          restoring
        />,
      );
      const uploadButton = screen.getByRole("button", { name: "上传视频" });
      const generateButton = [...container.querySelectorAll("button")].find((b) =>
        b.textContent?.trim(),
      );

      expect(uploadButton).toBeDisabled();
      expect(generateButton).toBeDisabled();
    });

    it("外部恢复态同样置 busy——重挂载后不放行第二次恢复", () => {
      // Narrow-subtab/wide-sidebar switches remount panels, losing VersionTimeMachine-local
      // restoration state; parents retain unit occupancy, so allowing writes would race the same final file.
      render(<UnitPreviewPanel unit={mkUnit()} projectName="proj" restoring />);
      expect(versionMachineBusy()).toBe(true);
    });

    it("恢复态上报带 unitId，供画布层按 unit 记录", () => {
      // Keep restoration outside panels: separate narrow/wide mount points unmount on switching
      // without canceling requests; selection reuse would also leak one local boolean across units.
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

  describe("链接带来的起始播放请求", () => {
    const readyUnit = () =>
      mkUnit({
        generated_assets: { ...mkUnit().generated_assets, video_clip: "reference_videos/E1U1.mp4", status: "completed" },
      });

    it("把针对本单元的请求交给播放器，针对其他单元的不理会", () => {
      render(<UnitPreviewPanel unit={readyUnit()} projectName="demo" status="ready" />);
      expect(screen.getByTestId("presentation-player")).not.toHaveAttribute("data-start-at");

      act(() => useAppStore.getState().requestPlaybackStart({ resource_type: "reference_videos", resource_id: "E1U2", seconds: 9 }));
      expect(screen.getByTestId("presentation-player")).not.toHaveAttribute("data-start-at");

      act(() => useAppStore.getState().requestPlaybackStart({ resource_type: "reference_videos", resource_id: "E1U1", seconds: 3.5 }));
      expect(screen.getByTestId("presentation-player")).toHaveAttribute("data-start-at", "3.5");
      act(() => useAppStore.setState({ playbackStart: null }));
    });

    it("不带时间点的请求不让播放器定位", () => {
      render(<UnitPreviewPanel unit={readyUnit()} projectName="demo" status="ready" />);
      act(() => useAppStore.getState().requestPlaybackStart({ resource_type: "reference_videos", resource_id: "E1U1", seconds: null }));
      expect(screen.getByTestId("presentation-player")).not.toHaveAttribute("data-start-at");
      act(() => useAppStore.setState({ playbackStart: null }));
    });
  });
});
