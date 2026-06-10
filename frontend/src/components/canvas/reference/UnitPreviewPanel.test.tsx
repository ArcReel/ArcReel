import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { UnitPreviewPanel } from "./UnitPreviewPanel";
import type { ReferenceVideoUnit } from "@/types";

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
});
