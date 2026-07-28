import { useState } from "react";
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
    expect(screen.getByTestId("picker-group-character")).toBeInTheDocument();
    expect(screen.getByTestId("picker-group-scene")).toBeInTheDocument();
    expect(screen.getByTestId("picker-group-prop")).toBeInTheDocument();
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
    expect(screen.queryByTestId("picker-group-scene")).not.toBeInTheDocument();
    expect(screen.queryByTestId("picker-group-prop")).not.toBeInTheDocument();
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

  it("closes on outside pointerdown", () => {
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

  it("does not close when pointerdown happens inside the listbox", () => {
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

  it("option has focus-visible ring class", () => {
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

  it("pointermove on option updates activeIndex; mouseenter at same coords does not", () => {
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

    // First establish a baseline pointer coordinate via real movement on alice.
    fireEvent.mouseMove(alice, { clientX: 10, clientY: 10 });
    expect(alice.getAttribute("aria-selected")).toBe("true");

    // Simulate "list scrolls under stationary cursor → bob enters cursor at the
    // same (10, 10) coords". mouseenter must NOT steal the keyboard selection.
    fireEvent.mouseEnter(bob, { clientX: 10, clientY: 10 });
    expect(alice.getAttribute("aria-selected")).toBe("true");
    expect(bob.getAttribute("aria-selected")).toBe("false");

    // Real pointer movement to bob → active moves.
    fireEvent.mouseMove(bob, { clientX: 20, clientY: 20 });
    expect(bob.getAttribute("aria-selected")).toBe("true");
    expect(alice.getAttribute("aria-selected")).toBe("false");
  });

  it("mouseenter at different coords after movement still honors real user movement", () => {
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

    fireEvent.mouseMove(alice, { clientX: 10, clientY: 10 });
    // User moves cursor into bob; browser fires mouseenter with new coords before
    // subsequent mousemove. Coord diff vs last ⇒ honor the move.
    fireEvent.mouseEnter(bob, { clientX: 30, clientY: 10 });
    expect(bob.getAttribute("aria-selected")).toBe("true");
  });

  it("renders four tabs (all/character/scene/prop) with counts", () => {
    render(
      <MentionPicker
        open
        query=""
        candidates={CANDIDATES}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(4);
    // 第一个 tab 是 "全部/All"，默认选中
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    // 全部 count = 2 (chars) + 1 (scene) + 1 (prop) = 4
    expect(tabs[0].textContent).toMatch(/4/);
  });

  it("activating the scene tab restricts visible options to that kind", async () => {
    const user = userEvent.setup();
    render(
      <MentionPicker
        open
        query=""
        candidates={CANDIDATES}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const sceneTab = screen.getAllByRole("tab")[2]; // all/character/scene/prop
    await user.click(sceneTab);
    expect(sceneTab).toHaveAttribute("aria-selected", "true");
    // 场景里的"酒馆"可见；角色里的"张三"被过滤掉
    expect(screen.getByRole("option", { name: /酒馆/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /张三/ })).not.toBeInTheDocument();
    // 单 tab 选中时 group 小标题被抑制（避免和 tab 语义重复）
    expect(screen.queryByTestId("picker-group-scene")).not.toBeInTheDocument();
  });

  it("Tab key completes the active mention like Enter", async () => {
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
    await user.keyboard("{Tab}");
    expect(onSelect).toHaveBeenCalledWith({ type: "character", name: "主角" });
  });

  it("renders an image thumbnail when imagePath + projectName are provided", () => {
    render(
      <MentionPicker
        open
        query="张三"
        candidates={CANDIDATES}
        projectName="proj"
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    // 张三 的 imagePath 在 fixture 里是 "/files/characters/zs.png"
    const img = screen.getByRole("option", { name: /张三/ }).querySelector("img");
    expect(img).not.toBeNull();
    expect(img!.getAttribute("src")).toMatch(/zs\.png/);
  });

  it("Shift+Tab does not trigger selection nor preventDefault (keeps a11y focus reversal)", () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(
      <MentionPicker
        open
        query=""
        candidates={CANDIDATES}
        onSelect={onSelect}
        onClose={onClose}
      />,
    );
    // picker 以 window keydown 监听；Shift+Tab 应当是 no-op（不插入、不吞 preventDefault）
    const event = new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, cancelable: true });
    window.dispatchEvent(event);
    expect(onSelect).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);

    // 普通 Tab（无 shift）仍补全并阻止默认（保留原有行为，防回归）
    const tabEvent = new KeyboardEvent("keydown", { key: "Tab", cancelable: true });
    window.dispatchEvent(tabEvent);
    expect(onSelect).toHaveBeenCalled();
    expect(tabEvent.defaultPrevented).toBe(true);
  });

  // #368: FloatingPortal should render the listbox as a direct descendant of
  // document.body (rather than inside the component's render container) so
  // that ancestor `overflow: hidden` / stacking contexts can never clip it.
  it("portals the listbox to document.body", () => {
    const { container } = render(
      <MentionPicker
        open
        query=""
        candidates={{ character: [{ name: "a", imagePath: null }], scene: [], prop: [] }}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const listbox = screen.getByRole("listbox");
    // listbox 不应出现在 render() 返回的测试容器子树里
    expect(container.contains(listbox)).toBe(false);
    // 但必须在 document.body 子树里
    expect(document.body.contains(listbox)).toBe(true);
  });

  it("does not close when pointerdown hits the anchorElement", () => {
    function Host({ onClose }: { onClose: () => void }) {
      // 用 state 承接按钮 DOM：与 ReferencePanel / ReferenceVideoCard 生产路径一致
      // （floating-ui 的 setReference 需在元素挂载后触发 re-render 才能感知到）。
      const [anchorEl, setAnchorEl] = useState<HTMLButtonElement | null>(null);
      return (
        <div>
          <button ref={setAnchorEl} data-testid="anchor" type="button">
            toggle
          </button>
          <MentionPicker
            open
            query=""
            candidates={{ character: [{ name: "a", imagePath: null }], scene: [], prop: [] }}
            anchorElement={anchorEl}
            onSelect={vi.fn()}
            onClose={onClose}
          />
        </div>
      );
    }
    const onClose = vi.fn();
    render(<Host onClose={onClose} />);
    fireEvent.pointerDown(screen.getByTestId("anchor"));
    expect(onClose).not.toHaveBeenCalled();
  });
});
