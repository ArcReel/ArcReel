import { useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n";
import { SortableHandle, SortableItem, SortableList, useSortableMove, type SortableMove } from "./SortableList";

const ROW_HEIGHT = 40;

function Row({ id }: { id: string }) {
  const { canMoveBy, moveBy } = useSortableMove(id);
  return (
    <SortableItem id={id}>
      <SortableHandle label={`调整「${id}」的顺序`} />
      {id}
      <button type="button" disabled={!canMoveBy(-1)} onClick={() => moveBy(-1)}>
        上移 {id}
      </button>
      <button type="button" disabled={!canMoveBy(1)} onClick={() => moveBy(1)}>
        下移 {id}
      </button>
    </SortableItem>
  );
}

function Harness({
  onMove,
  canMove,
  disabled,
}: {
  onMove?: (move: SortableMove<string>) => void;
  canMove?: (move: SortableMove<string>) => boolean;
  disabled?: boolean;
}) {
  const [ids, setIds] = useState(["甲", "乙", "丙"]);
  return (
    <SortableList
      ids={ids}
      getName={(id) => id}
      canMove={canMove}
      disabled={disabled}
      onMove={(move) => {
        onMove?.(move);
        setIds(move.ids);
      }}
    >
      <ul>
        {ids.map((id) => (
          <Row key={id} id={id} />
        ))}
      </ul>
    </SortableList>
  );
}

const order = () => screen.getAllByRole("listitem").map((item) => item.textContent?.match(/^(.)/)?.[1]);

describe("SortableList", () => {
  beforeEach(() => {
    // jsdom 不做布局：按条目在列表里的位置给出纵向排开的矩形，键盘拖动才有落点可算。
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const item = this.closest("li");
      const index = item ? Array.from(item.parentElement?.children ?? []).indexOf(item) : 0;
      return DOMRect.fromRect({ x: 0, y: index * ROW_HEIGHT, width: 300, height: ROW_HEIGHT });
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("moves an item with the keyboard and announces each step", async () => {
    const onMove = vi.fn();
    render(<Harness onMove={onMove} />);
    const handle = screen.getByRole("button", { name: "调整「甲」的顺序" });
    expect(handle).toHaveAttribute("aria-roledescription", "可排序的项");

    handle.focus();
    await userEvent.keyboard(" ");
    await waitFor(() => expect(screen.getByText(/已拿起「甲」，位于第 1 项，共 3 项/)).toBeInTheDocument());
    await userEvent.keyboard("{ArrowDown}");
    await waitFor(() => expect(screen.getByText(/「甲」移到第 2 项，共 3 项/)).toBeInTheDocument());
    await userEvent.keyboard(" ");

    await waitFor(() => expect(onMove).toHaveBeenCalledWith({ id: "甲", from: 0, to: 1, ids: ["乙", "甲", "丙"] }));
    expect(order()).toEqual(["乙", "甲", "丙"]);
  });

  it("puts the item back when the keyboard drag is cancelled", async () => {
    const onMove = vi.fn();
    render(<Harness onMove={onMove} />);

    screen.getByRole("button", { name: "调整「甲」的顺序" }).focus();
    await userEvent.keyboard(" ");
    await userEvent.keyboard("{ArrowDown}");
    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(screen.getByText(/已取消移动，「甲」回到第 1 项/)).toBeInTheDocument());
    expect(onMove).not.toHaveBeenCalled();
    expect(order()).toEqual(["甲", "乙", "丙"]);
  });

  it("moves by one step without dragging and disables moves past either end", async () => {
    const onMove = vi.fn();
    render(<Harness onMove={onMove} />);
    expect(screen.getByRole("button", { name: "上移 甲" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "下移 丙" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "上移 丙" }));

    expect(onMove).toHaveBeenCalledWith({ id: "丙", from: 2, to: 1, ids: ["甲", "丙", "乙"] });
    expect(order()).toEqual(["甲", "丙", "乙"]);
  });

  it("refuses moves rejected by canMove, also when dropped with the keyboard", async () => {
    const onMove = vi.fn();
    // 第一项固定在最前
    render(<Harness onMove={onMove} canMove={(move) => move.id !== "甲" && move.to !== 0} />);
    expect(screen.getByRole("button", { name: "上移 乙" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "下移 甲" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "下移 乙" })).toBeEnabled();

    screen.getByRole("button", { name: "调整「乙」的顺序" }).focus();
    await userEvent.keyboard(" ");
    await userEvent.keyboard("{ArrowUp}");
    await userEvent.keyboard(" ");

    await waitFor(() => expect(screen.getByText(/「乙」不能移到这里，已回到第 2 项/)).toBeInTheDocument());
    expect(onMove).not.toHaveBeenCalled();
    expect(order()).toEqual(["甲", "乙", "丙"]);
  });

  it("disables handles and step moves while the list is disabled", () => {
    render(<Harness disabled />);
    const list = screen.getByRole("list");

    for (const button of within(list).getAllByRole("button")) expect(button).toBeDisabled();
  });
});
