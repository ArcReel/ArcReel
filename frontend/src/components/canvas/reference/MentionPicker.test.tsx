import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MentionPicker } from "./MentionPicker";

const CANDIDATES = {
  character: [
    { name: "主角", imagePath: null },
    { name: "张三", imagePath: "/files/characters/zs.png" },
  ],
  scene: [{ name: "酒馆", imagePath: null }],
  prop: [{ name: "长剑", imagePath: null }],
};

describe("MentionPicker", () => {
  it("renders three group headers when all groups have items", () => {
    render(
      <MentionPicker
        open
        query=""
        candidates={CANDIDATES}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(/Characters|角色/)).toBeInTheDocument();
    expect(screen.getByText(/Scenes|场景/)).toBeInTheDocument();
    expect(screen.getByText(/Props|道具/)).toBeInTheDocument();
  });

  it("hides a group when it has no items after filtering", () => {
    render(
      <MentionPicker
        open
        query="主"
        candidates={CANDIDATES}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByText(/Scenes|场景/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Props|道具/)).not.toBeInTheDocument();
    expect(screen.getByText("主角")).toBeInTheDocument();
  });

  it("filters case-insensitively by substring", () => {
    const altCandidates = {
      character: [{ name: "Alice", imagePath: null }, { name: "Bob", imagePath: null }],
      scene: [],
      prop: [],
    };
    render(
      <MentionPicker
        open
        query="ali"
        candidates={altCandidates}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Alice")).toBeInTheDocument();
    expect(screen.queryByText("Bob")).not.toBeInTheDocument();
  });

  it("shows empty state when nothing matches", () => {
    render(
      <MentionPicker
        open
        query="xxxnomatch"
        candidates={CANDIDATES}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(/No matches|无匹配项/)).toBeInTheDocument();
  });

  it("invokes onSelect with {type,name} when an option is clicked", () => {
    const onSelect = vi.fn();
    render(
      <MentionPicker
        open
        query=""
        candidates={CANDIDATES}
        onSelect={onSelect}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("option", { name: /张三/ }));
    expect(onSelect).toHaveBeenCalledWith({ type: "character", name: "张三" });
  });

  it("supports ArrowDown/ArrowUp/Enter keyboard navigation", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <MentionPicker
        open
        query=""
        candidates={CANDIDATES}
        onSelect={onSelect}
        onClose={vi.fn()}
      />,
    );
    // First option is initially active
    await user.keyboard("{ArrowDown}{ArrowDown}");
    await user.keyboard("{Enter}");
    // After two ArrowDowns from the first (主角), we should be on 酒馆 (third overall)
    expect(onSelect).toHaveBeenCalledWith({ type: "scene", name: "酒馆" });
  });

  it("calls onClose when Escape is pressed", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <MentionPicker
        open
        query=""
        candidates={CANDIDATES}
        onSelect={vi.fn()}
        onClose={onClose}
      />,
    );
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("resets active highlight to the first option when query changes", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const { rerender } = render(
      <MentionPicker
        open
        query=""
        candidates={CANDIDATES}
        onSelect={onSelect}
        onClose={vi.fn()}
      />,
    );
    // Move highlight down twice (主角 → 张三 → 酒馆) then narrow the filter.
    await user.keyboard("{ArrowDown}{ArrowDown}");
    rerender(
      <MentionPicker
        open
        query="主"
        candidates={CANDIDATES}
        onSelect={onSelect}
        onClose={vi.fn()}
      />,
    );
    // After the filter change, Enter must pick the first item in the filtered
    // list (主角), not whatever was highlighted before.
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledWith({ type: "character", name: "主角" });
  });

  it("renders nothing when open=false", () => {
    const { container } = render(
      <MentionPicker
        open={false}
        query=""
        candidates={CANDIDATES}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("closes on outside pointerdown (#345)", () => {
    const onClose = vi.fn();
    render(
      <div>
        <button data-testid="outside">outside</button>
        <MentionPicker
          open
          query=""
          candidates={{ character: [{ name: "a", imagePath: null }], scene: [], prop: [] }}
          onSelect={vi.fn()}
          onClose={onClose}
        />
      </div>,
    );
    fireEvent.pointerDown(screen.getByTestId("outside"));
    expect(onClose).toHaveBeenCalled();
  });

  it("does not close when pointerdown happens inside the listbox (#345)", () => {
    const onClose = vi.fn();
    render(
      <MentionPicker
        open
        query=""
        candidates={{ character: [{ name: "a", imagePath: null }], scene: [], prop: [] }}
        onSelect={vi.fn()}
        onClose={onClose}
      />,
    );
    fireEvent.pointerDown(screen.getByRole("option", { name: /a/ }));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("option has focus-visible ring class (#345)", () => {
    render(
      <MentionPicker
        open
        query=""
        candidates={{ character: [{ name: "a", imagePath: null }], scene: [], prop: [] }}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const option = screen.getByRole("option", { name: /a/ });
    expect(option.className).toMatch(/focus-visible:ring/);
  });

  it("pointermove on option updates activeIndex; mouseenter without movement does not (#345)", () => {
    render(
      <MentionPicker
        open
        query=""
        candidates={{
          character: [
            { name: "alice", imagePath: null },
            { name: "bob", imagePath: null },
          ],
          scene: [],
          prop: [],
        }}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const alice = screen.getByRole("option", { name: /alice/ });
    const bob = screen.getByRole("option", { name: /bob/ });

    // Default activeIndex=0 → alice selected; mouseEnter alone (list scrolled under
    // a stationary cursor) must NOT steal the keyboard selection.
    fireEvent.mouseEnter(bob);
    expect(alice.getAttribute("aria-selected")).toBe("true");
    expect(bob.getAttribute("aria-selected")).toBe("false");

    // Real pointer movement → active moves to bob.
    fireEvent.mouseMove(bob, { clientX: 10, clientY: 10 });
    expect(bob.getAttribute("aria-selected")).toBe("true");
    expect(alice.getAttribute("aria-selected")).toBe("false");
  });
});
