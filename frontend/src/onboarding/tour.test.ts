import { describe, expect, it, vi } from "vitest";
import { anchorSelector, startTour, type TourLabels, type TourStep } from "./tour";

const LABELS: TourLabels = {
  next: "继续",
  prev: "上一步",
  done: "完成",
  skip: "跳过",
  close: "关闭引导",
  progress: (current, total) => `第 ${current} 步，共 ${total} 步`,
};

const TWO_STEPS: TourStep[] = [
  { anchor: null, title: "欢迎", body: "开场" },
  { anchor: null, title: "轮到你了", body: "收尾" },
];

function popover(): HTMLElement {
  const el = document.querySelector<HTMLElement>(".driver-popover");
  if (!el) throw new Error("popover not rendered");
  return el;
}

function click(selector: string): void {
  const el = popover().querySelector<HTMLElement>(selector);
  if (!el) throw new Error(`${selector} not found`);
  el.click();
}

describe("anchorSelector", () => {
  it("maps an anchor name to its data-onboarding selector", () => {
    expect(anchorSelector("new-project")).toBe('[data-onboarding="new-project"]');
  });
});

describe("startTour", () => {
  it("renders a centered popover with no highlighted element for anchor: null", () => {
    const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

    expect(popover().querySelector(".driver-popover-title")?.textContent).toBe("欢迎");
    // 页面上没有任何真实元素被高亮 —— driver 顶上的是自己的占位元素，气泡因此居中
    expect(document.querySelector(".driver-active-element")?.id).toBe("driver-dummy-element");

    handle.dispose();
  });

  it("uses the anchor's element when an anchor name is given", () => {
    const target = document.createElement("div");
    target.setAttribute("data-onboarding", "new-project");
    document.body.appendChild(target);

    const handle = startTour([{ anchor: "new-project", title: "入口", body: "在这里新建" }], LABELS, {
      onExit: vi.fn(),
    });

    expect(target.classList.contains("driver-active-element")).toBe(true);

    handle.dispose();
    target.remove();
  });

  it("renders one filmstrip cell per step, filled up to the current step", () => {
    const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

    const cells = () => Array.from(popover().querySelectorAll<HTMLElement>(".arc-tour-filmstrip span"));
    expect(cells().map((c) => c.dataset.on)).toEqual(["1", "0"]);

    click(".driver-popover-next-btn");
    expect(cells().map((c) => c.dataset.on)).toEqual(["1", "1"]);

    handle.dispose();
  });

  it("states the step position in text for screen readers", () => {
    const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

    expect(popover().querySelector(".arc-tour-sr-only")?.textContent).toBe("第 1 步，共 2 步");

    handle.dispose();
  });

  it("labels the close button", () => {
    const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

    expect(popover().querySelector(".driver-popover-close-btn")?.getAttribute("aria-label")).toBe("关闭引导");

    handle.dispose();
  });

  it("offers skip on every step but the last", () => {
    const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

    expect(popover().querySelector(".arc-tour-skip-btn")?.textContent).toBe("跳过");

    click(".driver-popover-next-btn");
    expect(popover().querySelector(".arc-tour-skip-btn")).toBeNull();

    handle.dispose();
  });

  it("reports the exit once when the tour is skipped", () => {
    const onExit = vi.fn();
    startTour(TWO_STEPS, LABELS, { onExit });

    click(".arc-tour-skip-btn");

    expect(onExit).toHaveBeenCalledTimes(1);
    expect(document.querySelector(".driver-popover")).toBeNull();
  });

  it("reports the exit once when the tour is closed", () => {
    const onExit = vi.fn();
    startTour(TWO_STEPS, LABELS, { onExit });

    click(".driver-popover-close-btn");

    expect(onExit).toHaveBeenCalledTimes(1);
  });

  it("reports the exit once when the tour is played to the end", () => {
    const onExit = vi.fn();
    startTour(TWO_STEPS, LABELS, { onExit });

    click(".driver-popover-next-btn");
    click(".driver-popover-next-btn");

    expect(onExit).toHaveBeenCalledTimes(1);
    expect(document.querySelector(".driver-popover")).toBeNull();
  });

  it("does not report an exit when the caller disposes the tour", () => {
    const onExit = vi.fn();
    const handle = startTour(TWO_STEPS, LABELS, { onExit });

    handle.dispose();

    expect(onExit).not.toHaveBeenCalled();
    expect(handle.isActive()).toBe(false);
  });
});
