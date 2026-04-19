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
});
