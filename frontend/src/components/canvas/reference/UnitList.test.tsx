import { afterEach, describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UnitList } from "./UnitList";
import type { ReferenceVideoUnit } from "@/types";

function mkUnit(id: string, overrides: Partial<ReferenceVideoUnit> = {}): ReferenceVideoUnit {
  return {
    unit_id: id,
    text: "@[张三] enter the pub",
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

describe("UnitList", () => {
  it("renders empty state when no units", () => {
    render(<UnitList units={[]} selectedId={null} onSelect={vi.fn()} onAdd={vi.fn()} />);
    expect(screen.getByText(/No video units in this episode yet|本集还没有视频单元/)).toBeInTheDocument();
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
    expect(screen.getByText("U1")).toBeInTheDocument();
    expect(screen.getByText("U2")).toBeInTheDocument();
    expect(screen.getAllByText(/enter the pub/)).toHaveLength(2);
    expect(screen.getByText(/^(3s|3 ?秒)$/)).toBeInTheDocument();
    expect(screen.getByText(/^(8s|8 ?秒)$/)).toBeInTheDocument();
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
    expect(screen.getByTestId("unit-row-E1U2")).toHaveAttribute("aria-current", "true");
    expect(screen.getByTestId("unit-row-E1U1")).not.toHaveAttribute("aria-current");
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
    fireEvent.click(screen.getByRole("button", { name: /Add video unit|新增视频单元/ }));
    expect(onAdd).toHaveBeenCalled();
  });

  describe("排序", () => {
    afterEach(() => vi.restoreAllMocks());

    function layoutRows() {
      // jsdom 不做布局：按条目在列表里的位置给出纵向排开的矩形，键盘拖动才有落点可算。
      vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
        const item = this.closest("li");
        const index = item ? Array.from(item.parentElement?.children ?? []).indexOf(item) : 0;
        return DOMRect.fromRect({ x: 0, y: index * 60, width: 300, height: 60 });
      });
    }

    it("用键盘把单元后移一位，提交为移到下一个单元之后", async () => {
      layoutRows();
      const onMove = vi.fn().mockResolvedValue(undefined);
      const units = [mkUnit("E1U1"), mkUnit("E1U2"), mkUnit("E1U3")];
      render(<UnitList units={units} selectedId={null} onSelect={vi.fn()} onAdd={vi.fn()} onMove={onMove} />);

      screen.getByRole("button", { name: /U1/, description: /空格|space/i }).focus();
      await userEvent.keyboard(" ");
      // dnd-kit 拿起后要等测量完成才响应方向键
      await waitFor(() => expect(screen.getByText(/已拿起|picked up/i)).toBeInTheDocument());
      await userEvent.keyboard("{ArrowDown}");
      await waitFor(() => expect(screen.getByText(/移到第 2 项|moved to position 2/i)).toBeInTheDocument());
      await userEvent.keyboard(" ");

      await waitFor(() => expect(onMove).toHaveBeenCalledWith("E1U1", "E1U2"));
    });

    it("按搜索词筛选时不能排序", () => {
      const units = [mkUnit("E1U1"), mkUnit("E1U2", { text: "另一段" })];
      render(<UnitList units={units} selectedId={null} onSelect={vi.fn()} onAdd={vi.fn()} onMove={vi.fn()} />);
      fireEvent.change(screen.getByRole("searchbox"), { target: { value: "pub" } });
      expect(screen.getByRole("button", { name: /U1/, description: /空格|space/i })).toBeDisabled();
    });
  });
});
