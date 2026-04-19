import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { UnitList } from "./UnitList";
import type { ReferenceVideoUnit } from "@/types";

function mkUnit(id: string, overrides: Partial<ReferenceVideoUnit> = {}): ReferenceVideoUnit {
  return {
    unit_id: id,
    shots: [{ duration: 3, text: "Shot 1 (3s): enter the pub" }],
    references: [{ type: "character", name: "张三" }],
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

describe("UnitList", () => {
  it("renders empty state when no units", () => {
    render(<UnitList units={[]} selectedId={null} onSelect={vi.fn()} onAdd={vi.fn()} />);
    expect(screen.getByText(/No units yet|尚未创建任何 Unit/)).toBeInTheDocument();
  });

  it("renders a row per unit with id, duration and prompt preview", () => {
    render(
      <UnitList
        units={[mkUnit("E1U1"), mkUnit("E1U2", { duration_seconds: 8 })]}
        selectedId={null}
        onSelect={vi.fn()}
        onAdd={vi.fn()}
      />,
    );
    expect(screen.getByText("E1U1")).toBeInTheDocument();
    expect(screen.getByText("E1U2")).toBeInTheDocument();
    expect(screen.getAllByText(/enter the pub/)).toHaveLength(2);
    expect(screen.getByText("3s")).toBeInTheDocument();
    expect(screen.getByText("8s")).toBeInTheDocument();
  });

  it("highlights the selected unit", () => {
    render(
      <UnitList
        units={[mkUnit("E1U1"), mkUnit("E1U2")]}
        selectedId="E1U2"
        onSelect={vi.fn()}
        onAdd={vi.fn()}
      />,
    );
    expect(screen.getByTestId("unit-row-E1U2")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("unit-row-E1U1")).toHaveAttribute("aria-selected", "false");
  });

  it("calls onSelect when a row is clicked", () => {
    const onSelect = vi.fn();
    render(
      <UnitList units={[mkUnit("E1U1")]} selectedId={null} onSelect={onSelect} onAdd={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId("unit-row-E1U1"));
    expect(onSelect).toHaveBeenCalledWith("E1U1");
  });

  it("calls onAdd when the 'new unit' button is clicked", () => {
    const onAdd = vi.fn();
    render(<UnitList units={[]} selectedId={null} onSelect={vi.fn()} onAdd={onAdd} />);
    fireEvent.click(screen.getByRole("button", { name: /New Unit|新建 Unit/ }));
    expect(onAdd).toHaveBeenCalled();
  });
});
