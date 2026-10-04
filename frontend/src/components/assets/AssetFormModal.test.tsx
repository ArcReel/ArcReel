import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { AssetFormModal } from "./AssetFormModal";

describe("AssetFormModal", () => {
  it("submits the entered name", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<AssetFormModal type="character" onClose={() => {}} onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText(/名称/), { target: { value: "王小明" } });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ name: "王小明" })));
  });

  it("shows voice_style field only for character type", () => {
    const { rerender } = render(<AssetFormModal type="character" onClose={() => {}} onSubmit={vi.fn()} />);
    expect(screen.getByLabelText(/声音风格/)).toBeInTheDocument();

    rerender(<AssetFormModal type="scene" onClose={() => {}} onSubmit={vi.fn()} />);
    expect(screen.queryByLabelText(/声音风格/)).not.toBeInTheDocument();
  });
});
