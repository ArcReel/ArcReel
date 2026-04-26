import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SegmentRefsEditModal } from "./SegmentRefsEditModal";
import type { Character, Prop, Scene } from "@/types";

const characters: Record<string, Character> = {
  Hero: { description: "main protagonist" },
  Villain: { description: "antagonist" },
  Mentor: { description: "guide" },
};
const scenes: Record<string, Scene> = {
  Forest: { description: "deep woods" },
};
const props: Record<string, Prop> = {
  Sword: { description: "legendary blade" },
};

const baseProps = {
  open: true,
  onClose: () => {},
  onSave: () => {},
  initialCharacters: ["Hero"],
  initialScenes: [],
  initialProps: [],
  characters,
  scenes,
  props,
  projectName: "demo",
};

describe("SegmentRefsEditModal", () => {
  it("does not render when open=false", () => {
    const { container } = render(
      <SegmentRefsEditModal {...baseProps} open={false} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders dialog with three sections (character/scene/prop)", () => {
    render(<SegmentRefsEditModal {...baseProps} />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    // Section headings appear (using i18n badge labels)
    expect(screen.getByText("角色")).toBeInTheDocument();
    expect(screen.getByText("场景")).toBeInTheDocument();
    expect(screen.getByText("道具")).toBeInTheDocument();
    // All character candidates listed
    expect(screen.getByRole("button", { name: /Hero/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Villain/ })).toBeInTheDocument();
  });

  it("clicking a row toggles selection without calling onSave (batch mode)", () => {
    const onSave = vi.fn();
    render(<SegmentRefsEditModal {...baseProps} onSave={onSave} />);

    const villainRow = screen.getByRole("button", { name: /Villain/ });
    expect(villainRow).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(villainRow);
    expect(villainRow).toHaveAttribute("aria-pressed", "true");
    expect(onSave).not.toHaveBeenCalled();
  });

  it("save button is disabled until at least one change is made", () => {
    render(<SegmentRefsEditModal {...baseProps} />);
    const saveBtn = screen.getByRole("button", { name: "保存" });
    expect(saveBtn).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /Villain/ }));
    expect(saveBtn).not.toBeDisabled();
  });

  it("clicking save calls onSave with only changed fields", () => {
    const onSave = vi.fn();
    render(<SegmentRefsEditModal {...baseProps} onSave={onSave} />);

    fireEvent.click(screen.getByRole("button", { name: /Villain/ })); // add char
    fireEvent.click(screen.getByRole("button", { name: /Forest/ })); // add scene

    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    // props unchanged → not in payload
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0]).toEqual({
      characters: ["Hero", "Villain"],
      scenes: ["Forest"],
    });
  });

  it("cancel does not call onSave and triggers onClose", () => {
    const onSave = vi.fn();
    const onClose = vi.fn();
    render(
      <SegmentRefsEditModal {...baseProps} onSave={onSave} onClose={onClose} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Villain/ }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onSave).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("renders stale references that exist in initial props but not in dictionaries", () => {
    render(
      <SegmentRefsEditModal
        {...baseProps}
        initialCharacters={["Hero", "Ghost"]}
      />,
    );
    const ghostRow = screen.getByRole("button", { name: /Ghost/ });
    expect(ghostRow).toBeInTheDocument();
    // Stale rows render with a hint title
    expect(ghostRow).toHaveAttribute("title", expect.stringContaining("失效"));
    // Removing the stale ref enables save
    fireEvent.click(ghostRow);
    expect(screen.getByRole("button", { name: "保存" })).not.toBeDisabled();
  });

  it("empty dictionary shows manage link button (kind passed to callback)", () => {
    const onManageClick = vi.fn();
    render(
      <SegmentRefsEditModal
        {...baseProps}
        characters={{}}
        initialCharacters={[]}
        onManageClick={onManageClick}
      />,
    );
    const manageLinks = screen.getAllByRole("button", { name: /前往管理/ });
    expect(manageLinks.length).toBeGreaterThan(0);
    fireEvent.click(manageLinks[0]);
    expect(onManageClick).toHaveBeenCalledWith("character");
  });

  it("search filters rows by name (case-insensitive)", () => {
    render(<SegmentRefsEditModal {...baseProps} />);
    const search = screen.getByPlaceholderText("搜索…");
    fireEvent.change(search, { target: { value: "vil" } });
    expect(screen.getByRole("button", { name: /Villain/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Mentor/ })).toBeNull();
  });

  it("close (X) button invokes onClose", () => {
    const onClose = vi.fn();
    render(<SegmentRefsEditModal {...baseProps} onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(onClose).toHaveBeenCalled();
  });
});
