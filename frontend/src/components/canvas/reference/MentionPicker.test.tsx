import { useState } from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useProjectsStore } from "@/stores/projects-store";
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
  it("builds 160px thumbnail URLs with the asset fingerprint from the projects store", () => {
    useProjectsStore.setState({ assetFingerprints: { "characters/zs.png": 1712345678901 } });
    const candidates = {
      character: [
        { name: "张三", imagePath: "characters/zs.png" },
        { name: "李四", imagePath: "characters/ls.png" },
      ],
      scene: [],
      prop: [],
    };
    render(
      <MentionPicker open query="" candidates={candidates} projectName="demo" onSelect={vi.fn()} onClose={vi.fn()} />,
    );
    const thumbs = screen.getAllByRole("option").map((option) => option.querySelector("img")?.getAttribute("src"));
    expect(thumbs).toEqual([
      "/api/v1/files/demo/characters/zs.png?v=1712345678901&w=160",
      "/api/v1/files/demo/characters/ls.png?w=160",
    ]);
    useProjectsStore.setState({ assetFingerprints: {} });
  });

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

  it("matches a candidate whose stored name and typed query differ in NFC/NFD form", () => {
    const nameNfd = "Hiếu".normalize("NFD");
    const queryNfc = "Hiếu".normalize("NFC");
    expect(nameNfd).not.toBe(queryNfc);
    const altCandidates = {
      character: [{ name: nameNfd, imagePath: null }],
      scene: [],
      prop: [],
    };
    render(
      <MentionPicker
        open
        query={queryNfc}
        candidates={altCandidates}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(nameNfd)).toBeInTheDocument();
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

  it("labels a derivative candidate as `本体 / 衍生` but selects the raw reference name", () => {
    const onSelect = vi.fn();
    render(
      <MentionPicker
        open
        query=""
        candidates={{ character: [{ name: "主角/劲装", imagePath: null }], scene: [], prop: [] }}
        onSelect={onSelect}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("option", { name: /主角 \/ 劲装/ }));
    expect(onSelect).toHaveBeenCalledWith({ type: "character", name: "主角/劲装" });
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
    await user.keyboard("{ArrowDown}{ArrowDown}");
    await user.keyboard("{Enter}");
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
    expect(container).toBeEmptyDOMElement();
  });

  it("closes on outside press", async () => {
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
    await userEvent.click(screen.getByTestId("outside"));
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

    fireEvent.mouseMove(alice, { clientX: 10, clientY: 10 });
    expect(alice).toHaveAttribute("aria-selected", "true");

    fireEvent.mouseEnter(bob, { clientX: 10, clientY: 10 });
    expect(alice).toHaveAttribute("aria-selected", "true");
    expect(bob).toHaveAttribute("aria-selected", "false");

    fireEvent.mouseMove(bob, { clientX: 20, clientY: 20 });
    expect(bob).toHaveAttribute("aria-selected", "true");
    expect(alice).toHaveAttribute("aria-selected", "false");
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
    fireEvent.mouseEnter(bob, { clientX: 30, clientY: 10 });
    expect(bob).toHaveAttribute("aria-selected", "true");
  });

  it("renders five tabs (all/product/character/scene/prop) with counts", () => {
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
    expect(tabs).toHaveLength(5);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(tabs[0]).toHaveTextContent(/4/);
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
    const sceneTab = screen.getByRole("tab", { name: /场景|Scene/i });
    await user.click(sceneTab);
    expect(sceneTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("option", { name: /酒馆/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /张三/ })).not.toBeInTheDocument();
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
    const img = screen.getByRole("option", { name: /张三/ }).querySelector("img");
    expect(img).not.toBeNull();
    expect(img!).toHaveAttribute("src", expect.stringMatching(/zs\.png/));
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
    const event = new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, cancelable: true });
    window.dispatchEvent(event);
    expect(onSelect).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);

    const tabEvent = new KeyboardEvent("keydown", { key: "Tab", cancelable: true });
    window.dispatchEvent(tabEvent);
    expect(onSelect).toHaveBeenCalled();
    expect(tabEvent.defaultPrevented).toBe(true);
  });

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
    expect(container.contains(listbox)).toBe(false);
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
