/**
 * Thin driver.js adapter.
 *
 * Separate business onboarding steps from driver.js APIs: steps describe anchors/copy;
 * anchor→selector mappings, labels, skins and exit convergence are configured once here. Additional
 * steps declare TourStep without touching driver configuration.
 *
 * `anchor` comes from `anchors.ts`, mapping to `[data-onboarding="<名字>"]`;
 * null means centered popovers without highlights, used for opening/closing. Anchors absent
 * from DOM after navigation or removal wait `anchorWaitMs`, then degrade
 * to centered explanations without skipping/stopping, recording `console.warn` evidence.
 */

import { driver, type Driver, type DriveStep, type PopoverDOM } from "driver.js";
import type { OnboardingAnchor } from "./anchors";

export interface TourStep {
  /** Anchor names → `[data-onboarding="…"]`; null centers popovers without highlights. */
  anchor: OnboardingAnchor | null;
  title: string;
  body: string;
  /**
   * Required routes are caller-defined and uninterpreted here, passed unchanged for
   * `onStepChange` navigation checks. Omission stays on current pages without route requirements,
   * commonly for centered steps beyond welcomes.
   */
  route?: string;
  /**
   * Required query parameters, passed unchanged like `route` without interpretation; callers compare/navigate sections
   * sharing pathnames, such as settings `section`.
   */
  query?: Record<string, string>;
  /**
   * Whether highlighted elements accept clicks; defaults inherit global `disableActiveInteraction: true`,
   * preventing explanations accidentally triggering generation. True only for navigational actions such as demo
   * entry, never writes; otherwise highlights remain unclickable throughout onboarding.
   */
  interactive?: boolean;
  /**
   * Destination prefixes including subroutes after `interactive` anchor clicks, caller-defined and
   * uninterpreted here. Passed unchanged to distinguish following intended entries from unrelated navigation;
   * former advances naturally, latter forces return. Meaningful only for true `interactive`.
   */
  interactiveTarget?: string;
}

export interface TourLabels {
  next: string;
  prev: string;
  done: string;
  skip: string;
  close: string;
  /** Accessible progress text, for example Step 1 of 2. */
  progress: (current: number, total: number) => string;
}

export interface TourHandle {
  /** Current zero-based indices, resuming after reconstruction instead of restarting. */
  currentIndex: () => number;
  /** Proactively dispose, for example unmount; never invoke onExit. */
  dispose: () => void;
}

/**
 * Share Dialog/Sheet `--scrim` color/opacity tokens. Drivers write inline SVG
 * `fill`, which supports CSS variables; fix opacity 1 without duplicate calculation.
 */
const OVERLAY_FILL = "var(--scrim)";

/** Mask/popover fade duration matches `duration-base`; driver 400ms defaults exceed motion limits. */
const FADE_MS = 200;

/**
 * Missing-anchor maximum waits in milliseconds. Driver MutationObserver waits for elements,
 * highlighting arrivals or centering popovers on timeout. Long enough for cross-page mounts,
 * without leaving users staring at empty masks.
 */
const ANCHOR_WAIT_MS = 1500;

export function anchorSelector(anchor: OnboardingAnchor): string {
  return `[data-onboarding="${anchor}"]`;
}

/**
 * Driver highlight movement/popover fades are element animations in its own styles, beyond `.arc-tour`
 * skin overrides. Reduced-motion users otherwise see eleven large cross-page movements; disable
 * driver animations entirely, instantly positioning each step without losing any explanation.
 *
 * `matchMedia` may be absent in unstubbed jsdom; absent means normal motion.
 */
function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * driver.js isolates underlying UI only through `pointer-events: none` and Tab-only focus traps,
 * never accessibility trees; screen-reader virtual cursors bypass both, reading/activating
 * underlying controls during onboarding. Explicitly mark existing body children `inert`, removing them
 * from accessibility trees and restoring on exit.
 *
 * Beyond `#app-root` (main.tsx), Dialog/Sheet portals attach
 * directly to `document.body`, siblings rather than descendants of `#app-root`;
 * `#app-root`-only inert misses already-open overlays. Snapshot direct body children at invocation
 * and inert each instead.
 *
 * Apply/remove whole isolation once at start/end. Never globally lift for `interactive` steps,
 * which would expose New project/other real cards/settings across `#app-root`, not only targets.
 * Use `openInteractiveHole` for target-only paths (below); drivers' overlay/popover remain
 * outside snapshots and unaffected.
 */
const DRIVER_PORTAL_SELECTOR = ".driver-overlay, .driver-popover, #driver-dummy-element";

let peripheralElements: Array<[element: HTMLElement, wasInert: boolean]> = [];
/** Whether isolation is active, preventing repeated snapshots between `interactive` steps from recording prior
 *  inert states as originals and making restoration impossible. */
let isolationApplied = false;

/**
 * `inert` cannot remove underlying overlays' global `document`/`window` keyboard listeners; Esc/Enter
 * handlers ignore accessibility membership,
 * silently closing overlays or submitting forms behind masks. Individual listener checks invite omissions;
 * centrally capture document keyboard events while active,
 * blocking all `keydown` except `Tab` for drivers' focus traps. Drivers' own
 * Esc/arrow handlers use `keyup`, not `keydown`, remaining unaffected.
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
 * Open target-only paths for `interactive` steps. Descendants cannot override `inert` (above),
 * so lift `inert` on every ancestor node from targets to `document.body`, while explicitly
 * marking sibling nodes `inert` at each level (most already `inert` through global isolation; handle
 * intermediate levels beyond top snapshots). Only target paths remain reachable,
 * never whole `#app-root`. Preserve ancestor-original `inert` values too, or
 * `closeInteractiveHole` restores siblings only and ancestors including `#app-root` remain
 * `inert = false` until `setPeripheralIsolation(false)` at tour end,
 * incorrectly retaining paths in subsequent steps.
 *
 * Exclude `DRIVER_PORTAL_SELECTOR` while marking siblings: at
 * `document.body`, `#app-root` siblings include driver overlay/popover/
 * dummy-element; including them locks Next/Previous/Close too.
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
 * Drivers add `aria-haspopup="dialog"`, `aria-expanded`, `aria-controls` to highlights, deleting all three
 * on departure. Invalid for div/section (axe aria-allowed-attr), and deletion erases preexisting trigger
 * attributes such as New project dropdowns. Inert underlying UI offers no screen-reader consumers,
 * so snapshot originals before highlights and restore after driver writes/deletes.
 */
const DRIVER_ARIA = ["aria-haspopup", "aria-expanded", "aria-controls"] as const;

/** Driver placeholders for centered popovers, unavailable in callbacks and queried by id. */
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
 * After structural/text changes, invoke once next frame. Drivers realign highlights only on resize/scroll;
 * newly rendered preceding content (lobby greetings appearing after Example projects when returning from settings)
 * pushes anchors without highlights following. Ignore attribute changes: driver alignment writes attributes only.
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

/** Decorative progress perforation tracks; sibling sr-only text carries semantics. */
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
 * Start onboarding.
 *
 * @param onExit Called once for skip/close/completion; `dispose()` never calls it.
 * @param startIndex Zero-based starting indices, default 0; rebuild from previous `currentIndex()`.
 * @param anchorWaitMs Missing-anchor maximum waits, default `ANCHOR_WAIT_MS`.
 * @param onStepChange Next/Previous button/arrow actions share internal callbacks,
 *   see `onNextClick`/`onPrevClick`; synchronously report upcoming indices
 *   before driver highlight changes. Callers navigate before new-anchor lookup;
 *   driver `waitForElement` absorbs natural timing differences without extra waits.
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
  const ariaSnapshots = new Map<Element, (string | null)[]>();
  // Whether highlights settled on current anchors. During driver framewise movements, avoid realignment;
  // defer intermediate page changes until settled onHighlighted.
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
    // Readonly throughout: driver `.driver-active *{pointer-events:none}` blocks underlying UI;
    // additionally disable highlighted interactions, preventing explanatory steps accidentally starting generation.
    disableActiveInteraction: true,
    showProgress: true,
    showButtons: ["next", "previous", "close"],
    // Missing anchors wait through driver MutationObserver before fallback, never skipping:
    // explanations remain valid without highlights; driver placeholders center popovers.
    waitForElement: anchorWaitMs,
    skipMissingElement: false,
    // Native arrow steps call moveNext()/movePrevious() directly, bypassing
    // onNextClick/onPrevClick and cross-page onStepChange. Disable those handlers,
    // taking over Esc/arrows in final keyup listeners so keyboard/button paths share
    // reporting.
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
    // Highlight callbacks receive undefined for driver placeholders. Declared-but-missing anchors
    // prove degradation already occurred; record evidence only here.
    //
    // Close prior holes on every transition; `interactive` steps reopen current-target-only paths,
    // leaving other `#app-root` contents (New project/other project cards/settings)
    // isolated rather than globally exposing `interactive` steps.
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
    // Driver already wrote current/deleted prior aria groups; restore both.
    // Popovers position at transition starts or animation midpoints; completion recalculates highlights only.
    // Anchors pushed by late greetings could overlap popovers, so realign again after settlement.
    onHighlighted: () => {
      restoreAria(ariaSnapshots);
      highlightSettled = true;
      instance.refresh();
    },
    // Converge exits here, never onDestroyed: that only fires after drivers write highlighted elements
    // into state through requestAnimationFrame; synchronous destroy or DOM-frameless
    // environments silently lose callbacks. Own button/proactive-dispose
    // entries guarantee every exit records once.
    onNextClick: () => handleNext(),
    onPrevClick: () => handlePrev(),
    onCloseClick: () => finish(),
    // Esc/mask clicks enter driver disposal, calling here before teardown.
    onDestroyStarted: () => finish(),
  });

  /** Next shared by buttons/`ArrowRight`, preserving cross-page reporting. */
  function handleNext(): void {
    if (instance.isLastStep()) {
      finish();
      return;
    }
    onStepChange?.((instance.getActiveIndex() ?? 0) + 1);
    instance.moveNext();
  }

  /**
   * Previous shared by buttons/`ArrowLeft`, preserving reporting. First steps have no predecessors;
   * driver disables buttons but keyboard paths lack that disabled state. Without guards,
   * `instance.movePrevious()` destroys entire tours when driver.js finds no preceding steps.
   */
  function handlePrev(): void {
    if (instance.isFirstStep()) return;
    onStepChange?.((instance.getActiveIndex() ?? 0) - 1);
    instance.movePrevious();
  }

  /** Record exits/dispose once, idempotently. */
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
    // Drivers restore focus to start-of-last-step elements, removed popover buttons, landing on body.
    // With modals already open before tours, Tab from body bypasses modal traps into backgrounds;
    // restore pretour focus unless navigation unmounted original elements.
    if (focusBeforeTour instanceof HTMLElement && focusBeforeTour.isConnected) focusBeforeTour.focus();
  }

  // Disabled allowKeyboardControl stops native Esc/arrows; take over: Esc finish()
  // matches driver-default allowClose true, never overridden here;
  // arrows reuse handleNext/handlePrev, sharing button reporting.
  function onKeyUp(e: KeyboardEvent): void {
    if (e.key === "Escape") {
      finish();
    } else if (e.key === "ArrowRight") {
      handleNext();
    } else if (e.key === "ArrowLeft") {
      handlePrev();
    }
  }

  // Isolation makes focused nodes inert and blurs them; record first.
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

/** Skip buttons: final steps have nothing to skip, showing Done only. */
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
