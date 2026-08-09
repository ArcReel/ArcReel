import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API, type AssetRenameResult } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { EditableAssetName } from "./EditableAssetName";

function renameResult(overrides: Partial<AssetRenameResult> = {}): AssetRenameResult {
  return {
    success: true,
    dry_run: true,
    old_name: "李白",
    new_name: "青莲",
    episodes: 2,
    references: 5,
    files: 3,
    ...overrides,
  };
}

describe("EditableAssetName", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");
  });

  afterEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
  });

  it("renders a plain heading without rename affordance when readOnly", () => {
    render(<EditableAssetName projectName="demo" name="李白" assetType="character" readOnly />);
    expect(screen.getByRole("heading", { name: "李白" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重命名" })).toBeNull();
  });

  it("previews the impact then executes rename on confirm", async () => {
    const renameSpy = vi
      .spyOn(API, "renameProjectAsset")
      .mockResolvedValueOnce(renameResult())
      .mockResolvedValueOnce(renameResult({ dry_run: false }));

    render(<EditableAssetName projectName="demo" name="李白" assetType="character" />);

    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    const input = screen.getByRole("textbox", { name: "重命名" });
    fireEvent.change(input, { target: { value: "  青莲  " } });
    fireEvent.keyDown(input, { key: "Enter" });

    // dry-run 预览先行，确认框展示影响数字
    await waitFor(() =>
      expect(renameSpy).toHaveBeenCalledWith("demo", "character", "李白", "青莲", { dryRun: true }),
    );
    expect(await screen.findByText("将更新 2 集共 5 处引用，重命名 3 个文件。")).toBeInTheDocument();

    // 编辑态下铅笔已隐藏，此时唯一名为「重命名」的按钮是确认框的确认按钮
    const dialogButtons = screen.getAllByRole("button", { name: "重命名" });
    fireEvent.click(dialogButtons[dialogButtons.length - 1]);

    await waitFor(() =>
      expect(renameSpy).toHaveBeenCalledWith("demo", "character", "李白", "青莲"),
    );
    await waitFor(() =>
      expect(useProjectsStore.getState().refreshProject).toHaveBeenCalledWith("demo"),
    );
  });

  it("warns instead of reporting failure when only the post-rename refresh fails", async () => {
    vi.spyOn(API, "renameProjectAsset")
      .mockResolvedValueOnce(renameResult())
      .mockResolvedValueOnce(renameResult({ dry_run: false }));
    // refreshProject 以结算值报告失败而不 reject，重命名本身已经成功
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("failed");

    render(<EditableAssetName projectName="demo" name="李白" assetType="character" />);

    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    const input = screen.getByRole("textbox", { name: "重命名" });
    fireEvent.change(input, { target: { value: "青莲" } });
    fireEvent.keyDown(input, { key: "Enter" });

    const dialogButtons = await screen.findAllByRole("button", { name: "重命名" });
    fireEvent.click(dialogButtons[dialogButtons.length - 1]);

    await waitFor(() => expect(useAppStore.getState().toast?.tone).toBe("warning"));
    expect(useAppStore.getState().toast?.text).toContain("刷新失败");
    expect(useAppStore.getState().toast?.text).not.toContain("重命名失败");
  });

  it("cancels on Escape without calling the API", () => {
    const renameSpy = vi.spyOn(API, "renameProjectAsset");
    render(<EditableAssetName projectName="demo" name="李白" assetType="character" />);

    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    const input = screen.getByRole("textbox", { name: "重命名" });
    fireEvent.change(input, { target: { value: "青莲" } });
    fireEvent.keyDown(input, { key: "Escape" });

    expect(renameSpy).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "李白" })).toBeInTheDocument();
  });

  it("exits edit mode without preview when the name is unchanged", async () => {
    const renameSpy = vi.spyOn(API, "renameProjectAsset");
    render(<EditableAssetName projectName="demo" name="李白" assetType="character" />);

    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    fireEvent.keyDown(screen.getByRole("textbox", { name: "重命名" }), { key: "Enter" });

    await waitFor(() => expect(screen.getByRole("heading", { name: "李白" })).toBeInTheDocument());
    expect(renameSpy).not.toHaveBeenCalled();
  });

  it("toasts and stays editable when the preview request fails", async () => {
    vi.spyOn(API, "renameProjectAsset").mockRejectedValue(new Error("同名冲突"));
    render(<EditableAssetName projectName="demo" name="李白" assetType="character" />);

    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    const input = screen.getByRole("textbox", { name: "重命名" });
    fireEvent.change(input, { target: { value: "青莲" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() =>
      expect(useAppStore.getState().toast?.text).toContain("同名冲突"),
    );
    expect(screen.getByRole("textbox", { name: "重命名" })).toBeInTheDocument();
  });
});
