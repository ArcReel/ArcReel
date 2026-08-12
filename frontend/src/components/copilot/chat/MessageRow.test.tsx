import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Turn } from "@/types";
import { MessageRow } from "./MessageRow";

const userTurn: Turn = {
  type: "user",
  uuid: "u-1",
  timestamp: "2026-05-02T14:21:00Z",
  content: [{ type: "text", text: "只改第 3 集" }],
};

const imageTurn: Turn = {
  type: "user",
  uuid: "u-2",
  timestamp: "2026-05-02T14:25:00Z",
  content: [
    { type: "image", source: { type: "base64", media_type: "image/png", data: "AAAA" } },
    { type: "text", text: "按这张图改人设" },
  ],
};

const twoImageTurn: Turn = {
  ...imageTurn,
  content: [
    { type: "image", source: { type: "base64", media_type: "image/png", data: "AAAA" } },
    { type: "image", source: { type: "base64", media_type: "image/jpeg", data: "BBBB" } },
    { type: "text", text: "按这两张图改人设" },
  ],
};

const imageOnlyTurn: Turn = {
  type: "user",
  uuid: "u-3",
  timestamp: "2026-05-02T14:27:00Z",
  content: [{ type: "image", source: { type: "base64", media_type: "image/png", data: "AAAA" } }],
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

  it("shows the edit entry but no copy button for an image-only user message", () => {
    render(<MessageRow turn={imageOnlyTurn} editable />);

    expect(screen.getByLabelText("编辑此消息并从这里重新发送")).toBeInTheDocument();
    expect(screen.queryByLabelText("复制消息")).not.toBeInTheDocument();
  });

  it("edits in place, showing the consequence note and submitting on ⌘/Ctrl+Enter", () => {
    const onSubmitEdit = vi.fn();
    render(<MessageRow turn={userTurn} editable editing onSubmitEdit={onSubmitEdit} />);

    const textarea = screen.getByLabelText("改写消息内容");
    expect(textarea).toHaveValue("只改第 3 集");
    expect(screen.getByText("此消息之后的对话将被丢弃，已产生的文件修改不会撤销")).toBeInTheDocument();

    fireEvent.change(textarea, { target: { value: "逐条给我看要改哪些台词" } });
    fireEvent.keyDown(textarea, { key: "Enter", metaKey: true });

    expect(onSubmitEdit).toHaveBeenCalledWith("u-1", "逐条给我看要改哪些台词", []);
  });

  it("carries the anchor's image attachments along with the rewritten text", () => {
    const onSubmitEdit = vi.fn();
    render(<MessageRow turn={imageTurn} editable editing onSubmitEdit={onSubmitEdit} />);

    const textarea = screen.getByLabelText("改写消息内容");
    expect(textarea).toHaveValue("按这张图改人设");

    fireEvent.change(textarea, { target: { value: "按这张图改场景" } });
    fireEvent.keyDown(textarea, { key: "Enter", metaKey: true });

    expect(onSubmitEdit).toHaveBeenCalledWith("u-2", "按这张图改场景", [
      { data: "AAAA", media_type: "image/png" },
    ]);
  });

  it("shows editable attachment thumbnails and submits only the images that remain", () => {
    const onSubmitEdit = vi.fn();
    render(<MessageRow turn={twoImageTurn} editable editing onSubmitEdit={onSubmitEdit} />);

    expect(screen.getByRole("img", { name: "编辑中的附件 1/2" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "编辑中的附件 2/2" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "移除编辑中的图片 1/2" }));

    expect(screen.getByRole("img", { name: "编辑中的附件 1/1" })).toBeInTheDocument();
    fireEvent.keyDown(screen.getByLabelText("改写消息内容"), { key: "Enter", metaKey: true });
    expect(onSubmitEdit).toHaveBeenCalledWith("u-2", "按这两张图改人设", [
      { data: "BBBB", media_type: "image/jpeg" },
    ]);
  });

  it("adds an image in the editor and includes it in the rewrite payload", async () => {
    const onSubmitEdit = vi.fn();
    render(<MessageRow turn={imageTurn} editable editing onSubmitEdit={onSubmitEdit} />);

    const added = new File(["new-image"], "new.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("上传附件图片"), { target: { files: [added] } });

    await waitFor(() => {
      expect(screen.getByRole("img", { name: "编辑中的附件 2/2" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "重新发送" }));

    expect(onSubmitEdit).toHaveBeenCalledWith("u-2", "按这张图改人设", [
      { data: "AAAA", media_type: "image/png" },
      { data: "bmV3LWltYWdl", media_type: "image/png" },
    ]);
  });

  it("disables resend when removing the final attachment leaves an empty draft", () => {
    render(<MessageRow turn={imageTurn} editable editing />);

    fireEvent.change(screen.getByLabelText("改写消息内容"), { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: "移除编辑中的图片 1/1" }));

    expect(screen.getByRole("button", { name: "重新发送" })).toBeDisabled();
  });

  it("lets a message with attachments be rewritten down to the attachments alone", () => {
    const onSubmitEdit = vi.fn();
    render(<MessageRow turn={imageTurn} editable editing onSubmitEdit={onSubmitEdit} />);

    fireEvent.change(screen.getByLabelText("改写消息内容"), { target: { value: "  " } });

    expect(screen.getByRole("button", { name: "重新发送" })).toBeEnabled();
    fireEvent.keyDown(screen.getByLabelText("改写消息内容"), { key: "Enter", metaKey: true });
    expect(onSubmitEdit).toHaveBeenCalledWith("u-2", "  ", [{ data: "AAAA", media_type: "image/png" }]);
  });

  it("keeps an empty draft unsubmittable on a text-only message", () => {
    const onSubmitEdit = vi.fn();
    render(<MessageRow turn={userTurn} editable editing onSubmitEdit={onSubmitEdit} />);

    fireEvent.change(screen.getByLabelText("改写消息内容"), { target: { value: "  " } });

    expect(screen.getByRole("button", { name: "重新发送" })).toBeDisabled();
    fireEvent.keyDown(screen.getByLabelText("改写消息内容"), { key: "Enter", metaKey: true });
    expect(onSubmitEdit).not.toHaveBeenCalled();
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

  it("keeps the composing key from submitting a half-typed candidate", () => {
    const onSubmitEdit = vi.fn();
    render(<MessageRow turn={userTurn} editable editing onSubmitEdit={onSubmitEdit} />);

    const textarea = screen.getByLabelText("改写消息内容");
    fireEvent.keyDown(textarea, { key: "Enter", metaKey: true, isComposing: true });
    fireEvent.keyDown(textarea, { key: "Enter", metaKey: true, keyCode: 229 });

    expect(onSubmitEdit).not.toHaveBeenCalled();

    fireEvent.keyDown(textarea, { key: "Enter", metaKey: true });
    expect(onSubmitEdit).toHaveBeenCalledOnce();
  });

  it("holds the cancel button while the rewrite is in flight", () => {
    const onCancelEdit = vi.fn();
    render(<MessageRow turn={userTurn} editable editing submitting onCancelEdit={onCancelEdit} />);

    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    fireEvent.keyDown(screen.getByLabelText("改写消息内容"), { key: "Escape" });

    expect(onCancelEdit).not.toHaveBeenCalled();
  });

  it("keeps the draft but locks resend once the turn is no longer editable", () => {
    const onSubmitEdit = vi.fn();
    const { rerender } = render(
      <MessageRow turn={userTurn} editable editing onSubmitEdit={onSubmitEdit} />,
    );
    fireEvent.change(screen.getByLabelText("改写消息内容"), { target: { value: "写到一半的草稿" } });

    // 会话在编辑期间开跑：草稿留着，重新发送锁住
    rerender(<MessageRow turn={userTurn} editable={false} editing onSubmitEdit={onSubmitEdit} />);

    expect(screen.getByLabelText("改写消息内容")).toHaveValue("写到一半的草稿");
    expect(screen.getByRole("button", { name: "重新发送" })).toBeDisabled();
    fireEvent.keyDown(screen.getByLabelText("改写消息内容"), { key: "Enter", metaKey: true });
    expect(onSubmitEdit).not.toHaveBeenCalled();
  });

  it("gives a streaming draft no action row", () => {
    render(<MessageRow turn={{ ...userTurn, type: "assistant" }} streaming />);

    expect(screen.queryByLabelText("复制消息")).not.toBeInTheDocument();
  });
});
