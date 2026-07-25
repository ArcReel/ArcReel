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

  it("opens at the requested step and reports where it stands", () => {
    const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn(), startIndex: 1 });

    expect(popover().querySelector(".driver-popover-title")?.textContent).toBe("轮到你了");
    expect(handle.currentIndex()).toBe(1);

    handle.dispose();
  });

  it("clamps an out-of-range start index onto the last step", () => {
    const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn(), startIndex: 9 });

    expect(handle.currentIndex()).toBe(1);

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
    expect(document.querySelector(".driver-popover")).toBeNull();
  });

  describe("peripheral isolation", () => {
    // driver.js 只挡 pointer-events + Tab 键，屏幕阅读器的虚拟光标仍能读到底层界面；
    // body 的既有子节点（#app-root，以及 ModalShell/CreateProjectModal 这类直接
    // createPortal 到 body、与 #app-root 是兄弟关系的对话框）打 inert 摘出无障碍树，
    // 才是真正的「全程只读」。
    function withAppRoot(): HTMLElement {
      const appRoot = document.createElement("div");
      appRoot.id = "app-root";
      document.body.appendChild(appRoot);
      return appRoot;
    }

    it("marks #app-root inert while the tour is active, and clears it on close", () => {
      const appRoot = withAppRoot();
      const onExit = vi.fn();
      startTour(TWO_STEPS, LABELS, { onExit });

      expect(appRoot.inert).toBe(true);

      click(".driver-popover-close-btn");

      expect(appRoot.inert).toBe(false);
      appRoot.remove();
    });

    it("clears #app-root inert when the caller disposes the tour", () => {
      const appRoot = withAppRoot();
      const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

      expect(appRoot.inert).toBe(true);

      handle.dispose();

      expect(appRoot.inert).toBe(false);
      appRoot.remove();
    });

    it("restores a peripheral element's pre-existing inert state instead of forcing it false", () => {
      const appRoot = withAppRoot();
      const preInerted = document.createElement("div");
      preInerted.inert = true;
      document.body.appendChild(preInerted);

      const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });
      expect(preInerted.inert).toBe(true);

      handle.dispose();

      expect(preInerted.inert).toBe(true);
      appRoot.remove();
      preInerted.remove();
    });

    it("also inerts a dialog already portaled to body when the tour starts, and clears it on dispose", () => {
      const appRoot = withAppRoot();
      // 模拟 ModalShell/CreateProjectModal 用 createPortal 挂到 body 的对话框——
      // 是 #app-root 的兄弟节点，不在其子树内。
      const modal = document.createElement("div");
      document.body.appendChild(modal);

      const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

      expect(modal.inert).toBe(true);

      handle.dispose();

      expect(modal.inert).toBe(false);
      appRoot.remove();
      modal.remove();
    });

    it("suppresses a peripheral document keydown listener while the tour is active, and restores it on dispose", () => {
      const appRoot = withAppRoot();
      const onKeyDown = vi.fn();
      document.addEventListener("keydown", onKeyDown);

      const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

      document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      expect(onKeyDown).not.toHaveBeenCalled();

      handle.dispose();

      document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      expect(onKeyDown).toHaveBeenCalledTimes(1);

      document.removeEventListener("keydown", onKeyDown);
      appRoot.remove();
    });

    it("does not suppress Tab so driver's own focus trap keeps working", () => {
      const appRoot = withAppRoot();
      const onKeyDown = vi.fn();
      document.addEventListener("keydown", onKeyDown);

      const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

      document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
      expect(onKeyDown).toHaveBeenCalledTimes(1);

      handle.dispose();
      document.removeEventListener("keydown", onKeyDown);
      appRoot.remove();
    });
  });
});
