import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { startTour, type TourLabels, type TourStep } from "@/onboarding/tour";
import { useEscapeClose } from "./useEscapeClose";

const LABELS: TourLabels = {
  next: "继续",
  prev: "上一步",
  done: "完成",
  skip: "跳过",
  close: "关闭引导",
  progress: (current, total) => `第 ${current} 步，共 ${total} 步`,
};

const ONE_STEP: TourStep[] = [{ anchor: null, title: "欢迎", body: "开场" }];

function pressEscape(): void {
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
}

describe("useEscapeClose", () => {
  afterEach(() => {
    document.querySelector("#app-root")?.remove();
  });

  it("calls onClose when Escape is pressed", () => {
    const onClose = vi.fn();
    renderHook(() => useEscapeClose(onClose));

    pressEscape();

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does nothing while disabled", () => {
    const onClose = vi.fn();
    renderHook(() => useEscapeClose(onClose, false));

    pressEscape();

    expect(onClose).not.toHaveBeenCalled();
  });

  it("defers to the onboarding tour's own Esc handling while it is active", () => {
    const appRoot = document.createElement("div");
    appRoot.id = "app-root";
    document.body.appendChild(appRoot);

    const onClose = vi.fn();
    renderHook(() => useEscapeClose(onClose));

    const handle = startTour(ONE_STEP, LABELS, { onExit: vi.fn() });

    pressEscape();
    expect(onClose).not.toHaveBeenCalled();

    handle.dispose();

    pressEscape();
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
