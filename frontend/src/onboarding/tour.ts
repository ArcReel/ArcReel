
import { driver, type Driver, type DriveStep, type PopoverDOM } from "driver.js";
import type { OnboardingAnchor } from "./anchors";

export interface TourStep {
  anchor: OnboardingAnchor | null;
  title: string;
  body: string;
  route?: string;
  query?: Record<string, string>;
  interactive?: boolean;
  interactiveTarget?: string;
}

export interface TourLabels {
  next: string;
  prev: string;
  done: string;
  skip: string;
  close: string;
  progress: (current: number, total: number) => string;
}

export interface TourHandle {
  currentIndex: () => number;
  dispose: () => void;
}

const OVERLAY_FILL = "var(--scrim)";

const FADE_MS = 200;

const ANCHOR_WAIT_MS = 1500;

export function anchorSelector(anchor: OnboardingAnchor): string {
  return `[data-onboarding="${anchor}"]`;
}

/**
 * driver 自带的高亮框位移与气泡淡入是元素级动画，写在它自己的样式里，`.arc-tour` 皮肤
 * 覆盖不到。声明了 reduced motion 的用户整场引导会看到 11 次跨页大幅位移，这里直接关掉
 * driver 的动画开关，改为逐步瞬间就位——讲解内容一字不少。
 *
 * `matchMedia` 在测试环境（jsdom 未 stub 时）可能缺席，取不到就按「不减弱」处理。
 */
function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * driver.js 只用 `pointer-events: none` 和一个仅拦截 Tab 键的焦点陷阱隔离底层界面，
 * 不触及无障碍树——屏幕阅读器的虚拟光标导航能绕开这两者，在引导期间直接读到并激活
 * 底层工作台的控件。这里显式给 body 的既有子节点打 `inert`，把它们从无障碍树摘除，
 * 引导退出时复原。
 *
 * 不止 `#app-root`（挂载点见 main.tsx）：Dialog、Sheet 等弹层经 Portal
 * 直接挂到 `document.body`，是 `#app-root` 的兄弟节点而非子孙，只
 * 打 `#app-root` 的 inert 罩不住"引导启动时已有弹窗开着"这种情形。这里改为在调用
 * 时刻快照 body 的直接子节点、逐个打 inert。
 *
 * 隔离只在引导启动/结束时整体施加/解除一次，`interactive` 步不整体解除——那样会
 * 把 `#app-root` 里「新建项目」、其他真实项目卡片、设置入口等一并开放，不只是目标
 * 元素。`interactive` 步改由 `openInteractiveHole` 单独凿一个只通向目标元素的孔
 * （见下方），driver 自己的 overlay/popover 因为一直被排除在快照之外，不受影响。
 */
const DRIVER_PORTAL_SELECTOR = ".driver-overlay, .driver-popover, #driver-dummy-element";

let peripheralElements: Array<[element: HTMLElement, wasInert: boolean]> = [];
let isolationApplied = false;

/**
 * `inert` 摘不掉底层弹窗自己挂在 `document`/`window` 上的全局键盘监听——Esc 关闭、
 * Enter 提交这类监听不看谁在无障碍树里，
 * 引导期间照样会被触发，在遮罩后台悄悄关弹窗、甚至提交表单。逐个让每个监听器自行
 * 判断引导状态属于挂一漏万，这里改为统一拦截：引导激活期间在 document 的捕获阶段
 * 拦下所有 `keydown`（`Tab` 除外，放行给 driver 自己的焦点陷阱）。driver 自己的
 * Esc/方向键处理挂在 `keyup` 而非 `keydown`，不受影响。
 */
let suspendKeyboard: (() => void) | null = null;

function setPeripheralIsolation(hidden: boolean): void {
  if (hidden === isolationApplied) return;
  isolationApplied = hidden;
  if (hidden) {
    peripheralElements = Array.from(document.body.children)
      .filter((el): el is HTMLElement => el instanceof HTMLElement)
      .filter((el) => !el.matches(DRIVER_PORTAL_SELECTOR))
      .map((el) => [el, Boolean(el.inert)]);
    peripheralElements.forEach(([el]) => {
      el.inert = true;
    });

    const onKeyDownCapture = (e: KeyboardEvent) => {
      if (e.key !== "Tab") e.stopPropagation();
    };
    document.addEventListener("keydown", onKeyDownCapture, true);
    suspendKeyboard = () => document.removeEventListener("keydown", onKeyDownCapture, true);
  } else {
    peripheralElements.forEach(([el, wasInert]) => {
      el.inert = wasInert;
    });
    peripheralElements = [];

    suspendKeyboard?.();
    suspendKeyboard = null;
  }
}

let interactiveHoleElements: Array<{ el: HTMLElement; prevInert: boolean }> = [];

/**
 * 为 `interactive` 步凿一个只通向目标元素的孔。`inert` 不能被后代自行覆盖（同上），
 * 因此要把目标元素到 `document.body` 祖先链上每一层节点自身的 `inert` 解除；同时
 * 把链上每层的其余兄弟节点显式打成 `inert`（多数已因整体隔离而是 `inert`，这里只
 * 处理链路本身此前未被顶层快照覆盖到的中间层），确保只有目标元素这一条路径可达，
 * 而不是连带打开整个 `#app-root`。链路节点自身原始的 `inert` 值也要记下——不记的话
 * `closeInteractiveHole` 只会复原兄弟节点，链路节点（含 `#app-root`）会一直停留在
 * `inert = false`，直到整场引导结束才被 `setPeripheralIsolation(false)` 顺带修正，
 * 期间的后续步骤都会误留这条路径可达。
 *
 * 打兄弟节点 inert 时同样要排除 `DRIVER_PORTAL_SELECTOR`——链路一路往上走到
 * `document.body` 时，`#app-root` 的兄弟节点里就包含 driver 自己的 overlay/popover/
 * dummy-element，不排除的话会连自己的 Next/Previous/Close 按钮一起锁死。
 */
function openInteractiveHole(target: HTMLElement): void {
  let node: HTMLElement | null = target;
  while (node && node !== document.body) {
    const parent: HTMLElement | null = node.parentElement;
    if (parent) {
      Array.from(parent.children).forEach((sibling) => {
        if (
          sibling === node ||
          !(sibling instanceof HTMLElement) ||
          sibling.inert ||
          sibling.matches(DRIVER_PORTAL_SELECTOR)
        )
          return;
        sibling.inert = true;
        interactiveHoleElements.push({ el: sibling, prevInert: false });
      });
    }
    interactiveHoleElements.push({ el: node, prevInert: node.inert });
    node.inert = false;
    node = parent;
  }
}

function closeInteractiveHole(): void {
  interactiveHoleElements.forEach(({ el, prevInert }) => {
    el.inert = prevInert;
  });
  interactiveHoleElements = [];
}

/**
 * driver 给高亮元素写 `aria-haspopup="dialog"`、`aria-expanded`、`aria-controls`，离开时把这三个
 * 属性整组删掉。这组属性写在 div、section 这类非交互元素上不合法（axe 的 aria-allowed-attr），
 * 删除时还会抹掉触发器自己的同名属性（如「新建项目」的下拉按钮）。引导期间界面整体是 inert，
 * 这组属性没有读屏可以宣读的对象，因此每次高亮前快照原值，driver 写完或删完后还原。
 */
const DRIVER_ARIA = ["aria-haspopup", "aria-expanded", "aria-controls"] as const;

/** 居中气泡时 driver 高亮的占位元素，回调里拿不到它，按 id 取。 */
const DRIVER_DUMMY_ID = "driver-dummy-element";

function snapshotAria(snapshots: Map<Element, (string | null)[]>, el: Element): void {
  if (!snapshots.has(el)) snapshots.set(el, DRIVER_ARIA.map((name) => el.getAttribute(name)));
}

function restoreAria(snapshots: Map<Element, (string | null)[]>): void {
  snapshots.forEach((values, el) => {
    DRIVER_ARIA.forEach((name, i) => {
      const value = values[i];
      if (value == null) el.removeAttribute(name);
      else el.setAttribute(name, value);
    });
  });
  const dummy = document.getElementById(DRIVER_DUMMY_ID);
  DRIVER_ARIA.forEach((name) => dummy?.removeAttribute(name));
}

/**
 * 页面结构变化时（增删节点、改文字）在下一帧回调一次。driver 只在窗口缩放与滚动时重新对齐
 * 高亮框；锚点高亮之后，排在它上方的内容才渲染出来（如从设置页回到大厅，问候区晚于「示例项目」
 * 区块出现），会把锚点推开，高亮框却留在原位。不观察属性变化：driver 自己对齐时只写属性。
 */
function observeLayoutChanges(onChange: () => void): () => void {
  let frame = 0;
  const observer = new MutationObserver(() => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      onChange();
    });
  });
  observer.observe(document.body, { childList: true, characterData: true, subtree: true });
  return () => {
    observer.disconnect();
    cancelAnimationFrame(frame);
  };
}

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

export function startTour(
  steps: TourStep[],
  labels: TourLabels,
  {
    onExit,
    startIndex = 0,
    anchorWaitMs = ANCHOR_WAIT_MS,
    onStepChange,
  }: {
    onExit: () => void;
    startIndex?: number;
    anchorWaitMs?: number;
    onStepChange?: (index: number) => void;
  },
): TourHandle {
  const total = steps.length;
  let exited = false;
  let disposing = false;
  const ariaSnapshots = new Map<Element, (string | null)[]>();
  let highlightSettled = false;

  const driveSteps: DriveStep[] = steps.map((step) => ({
    ...(step.anchor === null ? {} : { element: anchorSelector(step.anchor) }),
    data: { anchor: step.anchor, interactive: Boolean(step.interactive) },
    ...(step.interactive ? { disableActiveInteraction: false } : {}),
    popover: { title: step.title, description: step.body },
  }));

  const instance: Driver = driver({
    steps: driveSteps,
    popoverClass: "arc-tour",
    animate: !prefersReducedMotion(),
    duration: FADE_MS,
    overlayColor: OVERLAY_FILL,
    overlayOpacity: 1,
    stagePadding: 8,
    stageRadius: 10,
    disableActiveInteraction: true,
    showProgress: true,
    showButtons: ["next", "previous", "close"],
    waitForElement: anchorWaitMs,
    skipMissingElement: false,
    // driver 自带的方向键切步直接调 moveNext()/movePrevious()，不经过下面的
    // onNextClick/onPrevClick——跨页导航的 onStepChange 上报会被绕过。这里关闭它，
    // 改由本文件末尾的 keyup 监听统一接管 Esc/方向键，确保键盘路径与按钮路径走同一条
    // 上报逻辑。
    allowKeyboardControl: false,
    nextBtnText: labels.next,
    prevBtnText: labels.prev,
    doneBtnText: labels.done,
    onPopoverRender: (popover: PopoverDOM) => {
      const current = instance.getActiveIndex() ?? 0;
      popover.closeButton.setAttribute("aria-label", labels.close);
      renderProgress(popover.progress, current, total, labels.progress(current + 1, total));
      decorateSkip(popover, instance.isLastStep(), labels.skip);
    },
    onHighlightStarted: (element, step) => {
      const anchor = step.data?.anchor as OnboardingAnchor | undefined;
      if (anchor && !element) {
        console.warn(`[onboarding] anchor "${anchor}" not found; falling back to a centered popover`);
      }
      closeInteractiveHole();
      if (step.data?.interactive && element instanceof HTMLElement) {
        openInteractiveHole(element);
      }
      if (element) snapshotAria(ariaSnapshots, element);
      highlightSettled = false;
    },
    // 气泡在转场开始时（有动画时在半程）按锚点当时的位置摆好，落定时 driver 只重算高亮框：
    // 锚点在这之间被推开（问候区晚于「示例项目」区块渲染），气泡会压在锚点上，所以落定后再对齐一次。
    onHighlighted: () => {
      restoreAria(ariaSnapshots);
      highlightSettled = true;
      instance.refresh();
    },
    // 退出全部收口到这里，而不是 driver 的 onDestroyed。后者只在 driver 内部把高亮元素
    // 写进 state 之后才会触发，而那次写入排在 requestAnimationFrame 里 —— 同步 destroy
    // 与无 DOM 帧的环境下会静默漏掉回调。改走「按钮 + 主动收起」这两个我们自己掌握的
    // 入口，退出必然被记一次。
    onNextClick: () => handleNext(),
    onPrevClick: () => handlePrev(),
    onCloseClick: () => finish(),
    onDestroyStarted: () => finish(),
  });

  function handleNext(): void {
    if (instance.isLastStep()) {
      finish();
      return;
    }
    onStepChange?.((instance.getActiveIndex() ?? 0) + 1);
    instance.moveNext();
  }

  /**
   * 上一步：按钮点击与 `ArrowLeft` 键共用，保证跨页上报一致。首步无上一步可退——
   * 按钮此时被 driver 禁用不会触发，但键盘路径没有这层禁用态，不挡住的话
   * `instance.movePrevious()` 会在 driver.js 内部找不到上一步时把整个引导销毁掉。
   */
  function handlePrev(): void {
    if (instance.isFirstStep()) return;
    onStepChange?.((instance.getActiveIndex() ?? 0) - 1);
    instance.movePrevious();
  }

  function finish(): void {
    if (!exited && !disposing) {
      exited = true;
      onExit();
    }
    window.removeEventListener("keyup", onKeyUp);
    stopRealign();
    closeInteractiveHole();
    setPeripheralIsolation(false);
    instance.destroy();
    restoreAria(ariaSnapshots);
    // driver 把焦点还给最后一步开始时的焦点元素，那是已移除的气泡按钮，焦点于是落到 body。
    // 引导启动前开着模态对话框时，从 body 按 Tab 会越过对话框的焦点陷阱进到背景页面，
    // 这里还给引导启动前的焦点元素；它已随换页卸载时不处理。
    if (focusBeforeTour instanceof HTMLElement && focusBeforeTour.isConnected) focusBeforeTour.focus();
  }

  function onKeyUp(e: KeyboardEvent): void {
    if (e.key === "Escape") {
      finish();
    } else if (e.key === "ArrowRight") {
      handleNext();
    } else if (e.key === "ArrowLeft") {
      handlePrev();
    }
  }

  const focusBeforeTour = document.activeElement;
  setPeripheralIsolation(true);
  window.addEventListener("keyup", onKeyUp);
  const stopRealign = observeLayoutChanges(() => {
    if (highlightSettled) instance.refresh();
  });
  instance.drive(Math.min(Math.max(startIndex, 0), total - 1));

  return {
    currentIndex: () => instance.getActiveIndex() ?? 0,
    dispose: () => {
      disposing = true;
      window.removeEventListener("keyup", onKeyUp);
      stopRealign();
      closeInteractiveHole();
      setPeripheralIsolation(false);
      instance.destroy();
      restoreAria(ariaSnapshots);
    },
  };
}

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
