import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EditableEpisodeTitle } from "./EditableEpisodeTitle";

describe("EditableEpisodeTitle", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders a plain heading without edit affordance when canEdit is false", () => {
    render(<EditableEpisodeTitle title="第一集" canEdit={false} onSave={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "第一集" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑分集标题" })).toBeNull();
  });

  it("enters edit mode and saves the trimmed value on Enter", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<EditableEpisodeTitle title="旧标题" canEdit onSave={onSave} />);

    fireEvent.click(screen.getByRole("button", { name: "编辑分集标题" }));
    const input = screen.getByRole("textbox", { name: "编辑分集标题" });
    fireEvent.change(input, { target: { value: "  新标题  " } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onSave).toHaveBeenCalledWith("新标题"));
  });

  it("saves on the save button click", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<EditableEpisodeTitle title="旧标题" canEdit onSave={onSave} />);

    fireEvent.click(screen.getByRole("button", { name: "编辑分集标题" }));
    fireEvent.change(screen.getByRole("textbox", { name: "编辑分集标题" }), {
      target: { value: "改了" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledWith("改了"));
  });

  it("cancels on Escape, reverts to the original title, and never calls onSave", () => {
    const onSave = vi.fn();
    render(<EditableEpisodeTitle title="原标题" canEdit onSave={onSave} />);

    fireEvent.click(screen.getByRole("button", { name: "编辑分集标题" }));
    const input = screen.getByRole("textbox", { name: "编辑分集标题" });
    fireEvent.change(input, { target: { value: "改了一半" } });
    fireEvent.keyDown(input, { key: "Escape" });

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "原标题" })).toBeInTheDocument();
  });

  it("does not save on Enter while IME composition is active", () => {
    const onSave = vi.fn();
    render(<EditableEpisodeTitle title="原标题" canEdit onSave={onSave} />);

    fireEvent.click(screen.getByRole("button", { name: "编辑分集标题" }));
    const input = screen.getByRole("textbox", { name: "编辑分集标题" });
    fireEvent.change(input, { target: { value: "拼音确认中" } });
    // 中文输入法按 Enter 确认候选词时 isComposing 为 true，不应触发保存
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });

    expect(onSave).not.toHaveBeenCalled();
  });

  it("disables save for empty/whitespace input and does not call onSave on Enter", () => {
    const onSave = vi.fn();
    render(<EditableEpisodeTitle title="原标题" canEdit onSave={onSave} />);

    fireEvent.click(screen.getByRole("button", { name: "编辑分集标题" }));
    const input = screen.getByRole("textbox", { name: "编辑分集标题" });
    fireEvent.change(input, { target: { value: "   " } });

    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSave).not.toHaveBeenCalled();
  });
});
