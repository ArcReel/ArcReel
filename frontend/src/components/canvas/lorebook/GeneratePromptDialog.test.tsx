import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { API } from "@/api";
import { GeneratePromptDialog } from "./GeneratePromptDialog";

describe("GeneratePromptDialog", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("拉取预览文本并预填进可编辑文本框，加载完成前确认按钮禁用", async () => {
    vi.spyOn(API, "previewAssetGenerationPrompt").mockResolvedValue({
      asset_type: "character",
      resource_id: "Alice",
      prompt: "预览出的完整 prompt",
    });
    const onConfirm = vi.fn();
    render(
      <GeneratePromptDialog
        assetType="character"
        projectName="demo"
        resourceName="Alice"
        onClose={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    expect(screen.getByText("加载提示词中…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认生成" })).toBeDisabled();

    await waitFor(() => expect(screen.getByDisplayValue("预览出的完整 prompt")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "确认生成" })).toBeEnabled();
    expect(API.previewAssetGenerationPrompt).toHaveBeenCalledWith(
      "demo",
      "character",
      "Alice",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("确认时把编辑后的完整 prompt 与默认比例（16:9）一起回调，未选画质时不带 imageSize", async () => {
    vi.spyOn(API, "previewAssetGenerationPrompt").mockResolvedValue({
      asset_type: "scene",
      resource_id: "祠堂",
      prompt: "原始预览文本",
    });
    const onConfirm = vi.fn();
    render(
      <GeneratePromptDialog
        assetType="scene"
        projectName="demo"
        resourceName="祠堂"
        onClose={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    const textarea = await screen.findByDisplayValue("原始预览文本");
    fireEvent.change(textarea, { target: { value: "  用户改过的 prompt  " } });
    fireEvent.click(screen.getByRole("button", { name: "确认生成" }));

    expect(onConfirm).toHaveBeenCalledWith({ promptOverride: "用户改过的 prompt", aspectRatio: "16:9" });
  });

  it("选择比例与画质后一并带进回调", async () => {
    vi.spyOn(API, "previewAssetGenerationPrompt").mockResolvedValue({
      asset_type: "prop",
      resource_id: "玉佩",
      prompt: "道具 prompt",
    });
    const onConfirm = vi.fn();
    render(
      <GeneratePromptDialog
        assetType="prop"
        projectName="demo"
        resourceName="玉佩"
        onClose={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    await screen.findByDisplayValue("道具 prompt");
    fireEvent.change(screen.getByLabelText("画布比例"), { target: { value: "9:16" } });
    fireEvent.change(screen.getByLabelText("画质"), { target: { value: "2K" } });
    fireEvent.click(screen.getByRole("button", { name: "确认生成" }));

    expect(onConfirm).toHaveBeenCalledWith({
      promptOverride: "道具 prompt",
      aspectRatio: "9:16",
      imageSize: "2K",
    });
  });

  it("预览加载失败时显示错误与重试，重试成功后可以确认", async () => {
    const spy = vi
      .spyOn(API, "previewAssetGenerationPrompt")
      .mockRejectedValueOnce(new Error("网络错误"))
      .mockResolvedValueOnce({ asset_type: "product", resource_id: "保温杯", prompt: "商品 prompt" });
    render(
      <GeneratePromptDialog
        assetType="product"
        projectName="demo"
        resourceName="保温杯"
        onClose={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    await screen.findByText("提示词加载失败：网络错误");
    expect(screen.getByRole("button", { name: "确认生成" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(screen.getByDisplayValue("商品 prompt")).toBeInTheDocument());
    expect(spy).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "确认生成" })).toBeEnabled();
  });

  it("取消 / 关闭按钮触发 onClose，不触发确认", async () => {
    vi.spyOn(API, "previewAssetGenerationPrompt").mockResolvedValue({
      asset_type: "character",
      resource_id: "Alice",
      prompt: "prompt",
    });
    const onClose = vi.fn();
    const onConfirm = vi.fn();
    render(
      <GeneratePromptDialog
        assetType="character"
        projectName="demo"
        resourceName="Alice"
        onClose={onClose}
        onConfirm={onConfirm}
      />,
    );

    await screen.findByDisplayValue("prompt");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
