import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { UnitRail } from "./UnitRail";
import type { ReferenceVideoUnit } from "@/types";

function mkUnit(id: string): ReferenceVideoUnit {
  return {
    unit_id: id,
    text: "prompt",
    duration_seconds: 3,
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
  };
}

describe("UnitRail", () => {
  it("uses arrow keys to select adjacent units and navigate editor views", () => {
    const onSelect = vi.fn();
    const onNavigateView = vi.fn();
    render(
      <UnitRail
        units={[mkUnit("E1U1"), mkUnit("E1U2")]}
        selectedId="E1U1"
        onSelect={onSelect}
        onNavigateView={onNavigateView}
        onExpand={vi.fn()}
      />,
    );

    const first = screen.getByRole("button", { name: "U1" });
    const second = screen.getByRole("button", { name: "U2" });
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(onSelect).toHaveBeenLastCalledWith("E1U2");
    expect(second).toHaveFocus();

    fireEvent.keyDown(second, { key: "ArrowUp" });
    expect(onSelect).toHaveBeenLastCalledWith("E1U1");
    expect(first).toHaveFocus();

    fireEvent.keyDown(first, { key: "ArrowLeft" });
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(onNavigateView.mock.calls).toEqual([[-1], [1]]);
  });
});
