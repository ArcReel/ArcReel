import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { ConfirmAssetSheetsButton } from "./ConfirmAssetSheetsButton";

describe("ConfirmAssetSheetsButton", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
  });

  it("confirms every existing sheet through the shared endpoint", async () => {
    const confirmSpy = vi.spyOn(API, "confirmCurrentAssetSheets").mockResolvedValue({
      success: true,
      changed: true,
      confirmed_count: 9,
      confirmed: [],
    });
    const onReload = vi.fn().mockResolvedValue(true);
    render(<ConfirmAssetSheetsButton projectName="demo" onReload={onReload} />);

    fireEvent.click(screen.getByRole("button", { name: "确认现有素材" }));
    expect(screen.getByRole("dialog", { name: "将现有素材图确认为最新版本？" })).toBeInTheDocument();
    expect(screen.getByText(/只校正状态，不修改图片或版本历史/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "确认为最新" }));

    await waitFor(() => {
      expect(confirmSpy).toHaveBeenCalledWith("demo");
      expect(onReload).toHaveBeenCalledTimes(1);
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("does not change state when the user cancels", () => {
    const confirmSpy = vi.spyOn(API, "confirmCurrentAssetSheets");
    render(<ConfirmAssetSheetsButton projectName="demo" />);

    fireEvent.click(screen.getByRole("button", { name: "确认现有素材" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(confirmSpy).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
