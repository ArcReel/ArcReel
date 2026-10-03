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

    // 关闭按钮在指针移入或键盘聚焦提示区后才对读屏开放
    fireEvent.mouseEnter(await screen.findByRole("region", { name: "提示" }));
    fireEvent.click(await screen.findByRole("button", { name: "关闭提示" }));

    await waitFor(() => expect(screen.queryByText("已保存")).not.toBeInTheDocument());
  });
});
