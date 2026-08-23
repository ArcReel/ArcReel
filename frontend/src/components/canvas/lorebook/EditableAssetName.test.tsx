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
    expect(screen.queryByRole("button", { name: "重命名" })).not.toBeInTheDocument();
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

  it("rejects submission when the card starts its own write while the input is open", async () => {
    const renameSpy = vi.spyOn(API, "renameProjectAsset");
    const { rerender } = render(
      <EditableAssetName projectName="demo" name="李白" assetType="character" busy={false} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    const input = screen.getByRole("textbox", { name: "重命名" });
    fireEvent.change(input, { target: { value: "青莲" } });

    // 打开输入框后卡片自身起了一次写请求（上传立绘等），该占用只体现在本地 state 上
    rerender(<EditableAssetName projectName="demo" name="李白" assetType="character" busy />);
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() =>
      expect(useAppStore.getState().toast).toMatchObject({
        text: "该资产正在生成中，请等生成结束后再重命名",
        tone: "info",
      }),
    );
    expect(renameSpy).not.toHaveBeenCalled();
  });

  it("keeps the rename entry closed until the post-rename refresh settles", async () => {
    let releaseRefresh: (value: "success") => void = () => {};
    vi.spyOn(API, "renameProjectAsset")
      .mockResolvedValueOnce(renameResult())
      .mockResolvedValueOnce(renameResult({ dry_run: false }));
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockReturnValue(
      new Promise((resolve) => {
        releaseRefresh = resolve;
      }),
    );

    render(<EditableAssetName projectName="demo" name="李白" assetType="character" />);

    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    fireEvent.change(screen.getByRole("textbox", { name: "重命名" }), { target: { value: "青莲" } });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "重命名" }), { key: "Enter" });
    const dialogButtons = await screen.findAllByRole("button", { name: "重命名" });
    fireEvent.click(dialogButtons[dialogButtons.length - 1]);

    // 刷新未结算前 name 仍是旧值，此时重新进入编辑会拿过期基准名再提交一次
    const pencil = await screen.findByRole("button", { name: "重命名" });
    await waitFor(() => expect(pencil).toBeDisabled());

    releaseRefresh("success");
    await waitFor(() => expect(pencil).toBeEnabled());
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
