import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { AssetFormModal } from "./AssetFormModal";

describe("AssetFormModal", () => {
  it("create mode renders empty fields and calls onSubmit", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <AssetFormModal type="character" mode="create"
        onClose={() => {}} onSubmit={onSubmit} />
    );
    fireEvent.change(screen.getByLabelText(/名称/), { target: { value: "王小明" } });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ name: "王小明" })));
  });

  it("edit mode prefills fields", () => {
    render(
      <AssetFormModal
        type="scene" mode="edit"
        initialData={{ name: "庙宇", description: "阴森" }}
        onClose={() => {}} onSubmit={vi.fn()}
      />
    );
    expect(screen.getByDisplayValue("庙宇")).toBeInTheDocument();
    expect(screen.getByDisplayValue("阴森")).toBeInTheDocument();
  });

  it("import mode with conflict shows warning", () => {
    render(
      <AssetFormModal
        type="character" mode="import"
        initialData={{ name: "王", description: "" }}
        conflictWith={{ id: "1", type: "character", name: "王", description: "", voice_style: "", image_path: null, audio_path: null, source_project: null, updated_at: null }}
        onClose={() => {}} onSubmit={vi.fn()}
      />
    );
    expect(screen.getByText(/已有同名资产/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "覆盖已有" })).toBeInTheDocument();
  });

  it("shows voice_style field only for character type", () => {
    const { rerender } = render(
      <AssetFormModal type="character" mode="create"
        onClose={() => {}} onSubmit={vi.fn()} />
    );
    expect(screen.getByLabelText(/声音风格/)).toBeInTheDocument();

    rerender(
      <AssetFormModal type="scene" mode="create"
        onClose={() => {}} onSubmit={vi.fn()} />
    );
    expect(screen.queryByLabelText(/声音风格/)).not.toBeInTheDocument();
  });
});
