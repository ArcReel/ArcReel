/**
 * driver.js 薄适配层。
 *
 * 存在的理由是把「引导步骤」这个业务概念与 driver.js 的 API 隔开：步骤只描述锚点名和
 * 文案，锚点→选择器的映射、按钮文案、皮肤、退出路径收口都在这里一次性给定。后续段落
 * 新增步骤时只写 TourStep，不碰 driver 配置。
 *
 * 锚点约定：`anchor` 为锚点名时映射到 `[data-onboarding="<名字>"]`；为 null 时该步不
 * 高亮任何元素，driver 渲染居中气泡（开场与收尾用这个形态）。
 */

import { driver, type Driver, type DriveStep, type PopoverDOM } from "driver.js";

export interface TourStep {
  /** 锚点名 → `[data-onboarding="…"]`；null = 居中气泡，不高亮元素 */
  anchor: string | null;
  title: string;
  body: string;
}

export interface TourLabels {
  next: string;
  prev: string;
  done: string;
  skip: string;
  close: string;
  /** 进度的无障碍文本，如「第 1 步，共 2 步」 */
  progress: (current: number, total: number) => string;
}

export interface TourHandle {
  isActive: () => boolean;
  /** 主动收起（组件卸载等）。不触发 onExit。 */
  dispose: () => void;
}

/** 遮罩墨色 —— body 背景同色系的冷紫墨，而不是 driver 默认纯黑 */
const OVERLAY_INK = "oklch(0.10 0.012 265)";

export function anchorSelector(anchor: string): string {
  return `[data-onboarding="${anchor}"]`;
}

/** 进度齿孔轨道 —— 装饰，语义由同级的 sr-only 文本承载 */
function renderProgress(progress: HTMLElement, current: number, total: number, label: string): void {
  progress.replaceChildren();

  const strip = document.createElement("span");
  strip.className = "arc-tour-filmstrip";
  strip.setAttribute("aria-hidden", "true");
  for (let i = 0; i < total; i += 1) {
    const cell = document.createElement("span");
    cell.dataset.on = i <= current ? "1" : "0";
    strip.appendChild(cell);
  }

  const sr = document.createElement("span");
  sr.className = "arc-tour-sr-only";
  sr.textContent = label;

  progress.appendChild(strip);
  progress.appendChild(sr);
}

/**
 * 启动引导。
 *
 * @param onExit 任一退出路径（跳过 / 关闭 / 走完）都会调用一次；`dispose()` 不调用。
 */
export function startTour(
  steps: TourStep[],
  labels: TourLabels,
  { onExit }: { onExit: () => void },
): TourHandle {
  const total = steps.length;
  let exited = false;
  let disposing = false;

  const driveSteps: DriveStep[] = steps.map((step) => ({
    ...(step.anchor === null ? {} : { element: anchorSelector(step.anchor) }),
    popover: { title: step.title, description: step.body },
  }));

  const instance: Driver = driver({
    steps: driveSteps,
    popoverClass: "arc-tour",
    overlayColor: OVERLAY_INK,
    overlayOpacity: 0.78,
    stagePadding: 8,
    stageRadius: 10,
    // 全程只读：driver 的 `.driver-active *{pointer-events:none}` 已经封死了底层界面，
    // 这里再关掉高亮元素本身的交互，杜绝"讲到哪就能点到哪"意外触发生成动作。
    disableActiveInteraction: true,
    showProgress: true,
    showButtons: ["next", "previous", "close"],
    nextBtnText: labels.next,
    prevBtnText: labels.prev,
    doneBtnText: labels.done,
    onPopoverRender: (popover: PopoverDOM) => {
      const current = instance.getActiveIndex() ?? 0;
      popover.closeButton.setAttribute("aria-label", labels.close);
      renderProgress(popover.progress, current, total, labels.progress(current + 1, total));
      decorateSkip(popover, instance.isLastStep(), labels.skip);
    },
    // 退出全部收口到这里，而不是 driver 的 onDestroyed。后者只在 driver 内部把高亮元素
    // 写进 state 之后才会触发，而那次写入排在 requestAnimationFrame 里 —— 同步 destroy
    // 与无 DOM 帧的环境下会静默漏掉回调。改走「按钮 + 主动收起」这两个我们自己掌握的
    // 入口，退出必然被记一次。
    onNextClick: () => {
      if (instance.isLastStep()) finish();
      else instance.moveNext();
    },
    onPrevClick: () => instance.movePrevious(),
    onCloseClick: () => finish(),
    // Esc 与点击遮罩走 driver 内部的收起流程，在真正拆掉之前回调这里。
    onDestroyStarted: () => finish(),
  });

  /** 记一次退出并收起。重复调用只记一次。 */
  function finish(): void {
    if (!exited && !disposing) {
      exited = true;
      onExit();
    }
    instance.destroy();
  }

  instance.drive();

  return {
    isActive: () => instance.isActive(),
    dispose: () => {
      disposing = true;
      instance.destroy();
    },
  };
}

/** 「跳过」按钮 —— 最后一步没有可跳过的内容，只留「完成」 */
function decorateSkip(popover: PopoverDOM, isLastStep: boolean, label: string): void {
  popover.footer.querySelector(".arc-tour-skip-btn")?.remove();
  if (isLastStep) return;

  const skip = document.createElement("button");
  skip.type = "button";
  skip.className = "driver-popover-footer-btn arc-tour-skip-btn";
  skip.textContent = label;
  skip.addEventListener("click", () => popover.closeButton.click());
  popover.footer.insertBefore(skip, popover.footerButtons);
}
