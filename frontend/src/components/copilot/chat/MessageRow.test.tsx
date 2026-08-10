import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Turn } from "@/types";
import { MessageRow } from "./MessageRow";

const userTurn: Turn = {
  type: "user",
  uuid: "u-1",
  timestamp: "2026-05-02T14:21:00Z",
  content: [{ type: "text", text: "只改第 3 集" }],
};

describe("MessageRow", () => {
  it("renders the edit entry on an editable user message", () => {
    render(<MessageRow turn={userTurn} editable />);

    expect(screen.getByLabelText("编辑此消息并从这里重新发送")).toBeInTheDocument();
    expect(screen.getByLabelText("复制消息")).toBeInTheDocument();
  });

  it("hides the edit entry when not editable, keeping the rest of the action row", () => {
    render(<MessageRow turn={userTurn} editable={false} />);

    expect(screen.queryByLabelText("编辑此消息并从这里重新发送")).not.toBeInTheDocument();
    expect(screen.getByLabelText("复制消息")).toBeInTheDocument();
  });

  it("hands the anchor uuid and current text to the edit handler", () => {
    const onStartEdit = vi.fn();
    render(<MessageRow turn={userTurn} editable onStartEdit={onStartEdit} />);

    fireEvent.click(screen.getByLabelText("编辑此消息并从这里重新发送"));

    expect(onStartEdit).toHaveBeenCalledWith("u-1", "只改第 3 集");
  });

  it("edits in place, showing the consequence note and submitting on ⌘/Ctrl+Enter", () => {
    const onSubmitEdit = vi.fn();
    render(<MessageRow turn={userTurn} editable editing onSubmitEdit={onSubmitEdit} />);

    const textarea = screen.getByLabelText("改写消息内容");
    expect(textarea).toHaveValue("只改第 3 集");
    expect(screen.getByText("此消息之后的对话将被丢弃，已产生的文件修改不会撤销")).toBeInTheDocument();

    fireEvent.change(textarea, { target: { value: "逐条给我看要改哪些台词" } });
    fireEvent.keyDown(textarea, { key: "Enter", metaKey: true });

    expect(onSubmitEdit).toHaveBeenCalledWith("u-1", "逐条给我看要改哪些台词");
  });

  it("cancels the edit on Escape", () => {
    const onCancelEdit = vi.fn();
    render(<MessageRow turn={userTurn} editable editing onCancelEdit={onCancelEdit} />);

    fireEvent.keyDown(screen.getByLabelText("改写消息内容"), { key: "Escape" });

    expect(onCancelEdit).toHaveBeenCalled();
  });

  it("locks the editor while the rewrite is in flight", () => {
    const onSubmitEdit = vi.fn();
    render(<MessageRow turn={userTurn} editable editing submitting onSubmitEdit={onSubmitEdit} />);

    fireEvent.keyDown(screen.getByLabelText("改写消息内容"), { key: "Enter", ctrlKey: true });

    expect(onSubmitEdit).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "发送中…" })).toBeDisabled();
  });

  it("gives a streaming draft no action row", () => {
    render(<MessageRow turn={{ ...userTurn, type: "assistant" }} streaming />);

    expect(screen.queryByLabelText("复制消息")).not.toBeInTheDocument();
  });
});
