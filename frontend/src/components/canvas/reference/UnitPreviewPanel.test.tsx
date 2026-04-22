import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
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
    expect(screen.getAllByText(/Select a unit|选中左侧 Unit/).length).toBeGreaterThan(0);
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
});
