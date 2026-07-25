/**
 * driver.js 薄适配层。
 *
 * 存在的理由是把「引导步骤」这个业务概念与 driver.js 的 API 隔开：步骤只描述锚点名和
 * 文案，锚点→选择器的映射、按钮文案、皮肤、退出路径收口都在这里一次性给定。后续段落
 * 新增步骤时只写 TourStep，不碰 driver 配置。
 *
 * 锚点约定：`anchor` 取自 `anchors.ts` 的注册表，映射到 `[data-onboarding="<名字>"]`；
 * 为 null 时该步不高亮任何元素，driver 渲染居中气泡（开场与收尾用这个形态）。锚点当下
 * 不在 DOM 里（跨页导航后目标尚未渲染、或元素被改没了）时，等 `anchorWaitMs` 后降级
 * 成居中气泡继续讲，不跳步、不中止，同时 `console.warn` 留线索。
 */

import { driver, type Driver, type DriveStep, type PopoverDOM } from "driver.js";
import type { OnboardingAnchor } from "./anchors";

export interface TourStep {
  /** 锚点名 → `[data-onboarding="…"]`；null = 居中气泡，不高亮元素 */
  anchor: OnboardingAnchor | null;
  title: string;
  body: string;
  /**
   * 该步所需的路由，调用方自行定义、这里不解释语义——纯粹原样透传，供调用方在
   * `onStepChange` 里比对是否需要导航。省略 = 不要求特定路由，留在当前页继续讲
   * （开场欢迎气泡以外的居中步通常这样用）。
   */
  route?: string;
  /**
   * 该步是否允许点击高亮元素本身。默认继承全局 `disableActiveInteraction: true`
   * （防止讲到哪点到哪，意外触发生成动作）。仅当该步的落点动作就是导航（如点进演示
   * 工作台）而非写操作时才置 true——否则高亮元素在整个引导期间都点不到。
   */
  interactive?: boolean;
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
  /** 当前停在第几步（0 基）。用于重建时接着讲，而不是退回开头。 */
  currentIndex: () => number;
  /** 主动收起（组件卸载等）。不触发 onExit。 */
  dispose: () => void;
}

/** 遮罩墨色 —— body 背景同色系的冷紫墨，而不是 driver 默认纯黑 */
const OVERLAY_INK = "oklch(0.10 0.012 265)";

/**
 * 锚点缺席时的等待上限（毫秒）。driver 在这段时间里挂 MutationObserver 等元素出现，
 * 等到就正常高亮，等不到就降级居中气泡。够跨页导航后目标组件挂载完，又不至于让用户
 * 对着空遮罩发呆。
 */
export const ANCHOR_WAIT_MS = 1500;

export function anchorSelector(anchor: OnboardingAnchor): string {
  return `[data-onboarding="${anchor}"]`;
}

/**
 * driver.js 只用 `pointer-events: none` 和一个仅拦截 Tab 键的焦点陷阱隔离底层界面，
 * 不触及无障碍树——屏幕阅读器的虚拟光标导航能绕开这两者，在引导期间直接读到并激活
 * 底层工作台的控件。这里显式给 body 的既有子节点打 `inert`，把它们从无障碍树摘除，
 * 引导退出时复原。
 *
 * 不止 `#app-root`（挂载点见 main.tsx）：`ModalShell`/`CreateProjectModal` 等对话框
 * 用 `createPortal` 直接挂到 `document.body`，是 `#app-root` 的兄弟节点而非子孙，只
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
/** 当前是否已施加隔离——避免 `interactive` 步之间来回切换时重复快照，把「已因上次
 *  隔离而变 inert」的状态误当作原始值记下，导致复原时回不去。 */
let isolationApplied = false;

/**
 * `inert` 摘不掉底层弹窗自己挂在 `document`/`window` 上的全局键盘监听——Esc 关闭、
 * Enter 提交（如 `ApiKeysTab` 的「新建 API Key」弹窗）这类监听不看谁在无障碍树里，
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
 * @param startIndex 从第几步开始（0 基）。默认 0；重建时传入上一次的 `currentIndex()`。
 * @param anchorWaitMs 锚点缺席时的等待上限，默认 `ANCHOR_WAIT_MS`。
 * @param onStepChange 「下一步/上一步」被触发时（按钮点击与方向键都走同一条内部回调，
 *   见 `onNextClick`/`onPrevClick`）、在 driver 实际切换高亮之前，同步上报即将停靠的
 *   步号。调用方可以据此在 driver 尝试高亮新步骤的锚点之前先行导航——两者天然存在的
 *   时间差由 driver 自己的 `waitForElement` 轮询吸收，不需要额外的等待逻辑。
 */
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

  const driveSteps: DriveStep[] = steps.map((step) => ({
    ...(step.anchor === null ? {} : { element: anchorSelector(step.anchor) }),
    data: { anchor: step.anchor, interactive: Boolean(step.interactive) },
    ...(step.interactive ? { disableActiveInteraction: false } : {}),
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
    // 锚点缺席时先等一会儿（driver 内部挂 MutationObserver），超时后不跳过这一步 ——
    // 讲解本身仍然成立，丢的只是高亮，driver 会退回自己的占位元素、把气泡摆到屏幕中央。
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
    // 高亮到的元素是 driver 的占位元素时，回调收到的 element 是 undefined。步骤本来就
    // 声明了锚点却落到这里，说明锚点在页面上找不到 —— 降级已经发生，这里只负责留线索。
    //
    // 每次切换先收起上一步可能凿开的孔，`interactive` 步再针对当前目标元素重新凿一个——
    // 只让目标元素可达，`#app-root` 里其余内容（新建项目、其他项目卡片、设置入口等）
    // 仍保持隔离，不因为这一步是 interactive 就整体开放。
    onHighlightStarted: (element, step) => {
      const anchor = step.data?.anchor as OnboardingAnchor | undefined;
      if (anchor && !element) {
        console.warn(`[onboarding] anchor "${anchor}" not found; falling back to a centered popover`);
      }
      closeInteractiveHole();
      if (step.data?.interactive && element instanceof HTMLElement) {
        openInteractiveHole(element);
      }
    },
    // 退出全部收口到这里，而不是 driver 的 onDestroyed。后者只在 driver 内部把高亮元素
    // 写进 state 之后才会触发，而那次写入排在 requestAnimationFrame 里 —— 同步 destroy
    // 与无 DOM 帧的环境下会静默漏掉回调。改走「按钮 + 主动收起」这两个我们自己掌握的
    // 入口，退出必然被记一次。
    onNextClick: () => handleNext(),
    onPrevClick: () => handlePrev(),
    onCloseClick: () => finish(),
    // Esc 与点击遮罩走 driver 内部的收起流程，在真正拆掉之前回调这里。
    onDestroyStarted: () => finish(),
  });

  /** 下一步：按钮点击与 `ArrowRight` 键共用，保证跨页上报一致。 */
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

  /** 记一次退出并收起。重复调用只记一次。 */
  function finish(): void {
    if (!exited && !disposing) {
      exited = true;
      onExit();
    }
    window.removeEventListener("keyup", onKeyUp);
    closeInteractiveHole();
    setPeripheralIsolation(false);
    instance.destroy();
  }

  // allowKeyboardControl 关闭后 driver 不再自行处理 Esc/方向键，这里接管：Esc 走 finish()
  // （与 driver 默认的 allowClose 行为一致，本文件未覆盖该配置，其默认值即为 true）；
  // 方向键复用 handleNext/handlePrev，与按钮点击走同一条上报路径。
  function onKeyUp(e: KeyboardEvent): void {
    if (e.key === "Escape") {
      finish();
    } else if (e.key === "ArrowRight") {
      handleNext();
    } else if (e.key === "ArrowLeft") {
      handlePrev();
    }
  }

  setPeripheralIsolation(true);
  window.addEventListener("keyup", onKeyUp);
  instance.drive(Math.min(Math.max(startIndex, 0), total - 1));

  return {
    currentIndex: () => instance.getActiveIndex() ?? 0,
    dispose: () => {
      disposing = true;
      window.removeEventListener("keyup", onKeyUp);
      closeInteractiveHole();
      setPeripheralIsolation(false);
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
