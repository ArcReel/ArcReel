import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "@/stores/app-store";
import { ToastOverlay } from "./ToastOverlay";

describe("ToastOverlay", () => {
  beforeEach(() => {
    useAppStore.setState({ toast: null });
  });

  it("shows each pushed toast", async () => {
    render(<ToastOverlay />);
    act(() => useAppStore.getState().pushToast("已保存", "success"));
    act(() => useAppStore.getState().pushToast("已导出", "success"));

    expect(await screen.findByText("已保存")).toBeInTheDocument();
    expect(screen.getByText("已导出")).toBeInTheDocument();
  });

  it("shows every toast pushed within the same batch, including those with undo", async () => {
    const undo = vi.fn();
    render(<ToastOverlay />);
    act(() => {
      useAppStore.getState().pushToast("已删除分镜", "info", { action: { label: "撤销", onClick: undo } });
      useAppStore.getState().pushNotification("视频生成失败", "error");
    });

    expect(await screen.findByText("已删除分镜")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "撤销" })).toBeInTheDocument();
    // Error notices also have immediate screen-reader announcement copies.
    expect(screen.getAllByText("视频生成失败").length).toBeGreaterThan(0);
  });

  it("runs the undo action and dismisses the toast", async () => {
    const undo = vi.fn();
    render(<ToastOverlay />);
    act(() => useAppStore.getState().pushToast("已删除分镜", "info", { action: { label: "撤销", onClick: undo } }));

    fireEvent.click(await screen.findByRole("button", { name: "撤销" }));

    expect(undo).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByText("已删除分镜")).not.toBeInTheDocument());
  });

  it("dismisses the toast from its close button", async () => {
    render(<ToastOverlay />);
    act(() => useAppStore.getState().pushToast("已保存", "success"));

    // Close buttons become screen-reader-accessible only after pointer entry/keyboard focus within notices.
    fireEvent.mouseEnter(await screen.findByRole("region", { name: "提示" }));
    fireEvent.click(await screen.findByRole("button", { name: "关闭提示" }));

    await waitFor(() => expect(screen.queryByText("已保存")).not.toBeInTheDocument());
  });
});
