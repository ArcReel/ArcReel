import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
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
    render(<UnitPreviewPanel unit={null} onGenerate={vi.fn()} generating={false} />);
    expect(screen.getByText(/Select a unit|选中左侧 Unit/)).toBeInTheDocument();
  });

  it("shows generate button for pending unit", () => {
    const onGenerate = vi.fn();
    render(<UnitPreviewPanel unit={mkUnit()} onGenerate={onGenerate} generating={false} />);
    const btn = screen.getByRole("button", { name: /Generate video|生成视频/ });
    fireEvent.click(btn);
    expect(onGenerate).toHaveBeenCalledWith("E1U1");
  });

  it("disables button and shows generating label while running", () => {
    render(
      <UnitPreviewPanel
        unit={mkUnit()}
        onGenerate={vi.fn()}
        generating={true}
      />,
    );
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
    expect(screen.getByText(/Generating|生成中/)).toBeInTheDocument();
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
      <UnitPreviewPanel unit={unit} onGenerate={vi.fn()} generating={false} projectName="proj" />,
    );
    expect(container.querySelector("video")).toBeInTheDocument();
  });
});
