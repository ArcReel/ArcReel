import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import "@/i18n";
import { API } from "@/api";
import { ManualEntryDialog } from "./ManualEntryDialog";

describe("ManualEntryDialog", () => {
  it("stops blocking confirm after switching media type away from a field left invalid", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "getNewEntryHints").mockResolvedValue({ existing: null, durations: null });
    const onConfirm = vi.fn();
    render(<ManualEntryDialog open onOpenChange={vi.fn()} initialName="relay-x" onConfirm={onConfirm} />);

    const dialog = await screen.findByRole("dialog", { name: "手动填写模型信息" });
    await user.type(within(dialog).getByLabelText("模型厂商"), "acme");
    // 默认类型是文本：最大输出长度填 0 不合法，拦下确定
    await user.type(within(dialog).getByLabelText("「最大输出长度」的值"), "0");
    expect(within(dialog).getByRole("button", { name: "确定" })).toBeDisabled();

    await user.click(within(dialog).getByRole("button", { name: "图片" }));
    const confirm = within(dialog).getByRole("button", { name: "确定" });
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledWith(expect.objectContaining({ maker: "acme", name: "relay-x", media_type: "image" }));
  });
});
