import { useEffect, useRef } from "react";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import type { WorkspaceFocusTarget } from "@/types";

/** 定位后跟随布局变化重新对齐的时长：跨页跳转后，新页面的布局在定位之后的几帧里才稳定。 */
const SETTLE_MS = 1000;
const USER_SCROLL_EVENTS = ["wheel", "touchstart", "pointerdown", "keydown"] as const;

function scrollParentOf(el: HTMLElement): HTMLElement | null {
  for (let node = el.parentElement; node; node = node.parentElement) {
    const { overflowY } = getComputedStyle(node);
    if (overflowY === "auto" || overflowY === "scroll") return node;
  }
  return null;
}

/**
 * 把元素滚到视野中央，并在随后的 {@link SETTLE_MS} 内随它或所在滚动容器的尺寸变化重新对齐：跨页跳转时
 * 新页面刚挂载，滚动容器可能还没收到最终高度，只滚一次会停在按旧布局算出的位置。观察开始时的首次回调
 * 也重新对齐，因为它带的可能已是变化后的尺寸。用户开始滚动或操作时停止跟随。返回停止跟随的函数。
 */
function scrollIntoViewUntilSettled(el: HTMLElement): () => void {
  const align = () => el.scrollIntoView({ behavior: "smooth", block: "center" });
  align();
  if (typeof ResizeObserver === "undefined") return () => {};
  const observer = new ResizeObserver(align);
  observer.observe(el);
  const scrollParent = scrollParentOf(el);
  if (scrollParent) observer.observe(scrollParent);
  const stop = () => {
    observer.disconnect();
    clearTimeout(timer);
    for (const type of USER_SCROLL_EVENTS) window.removeEventListener(type, stop, true);
  };
  const timer = setTimeout(stop, SETTLE_MS);
  for (const type of USER_SCROLL_EVENTS) window.addEventListener(type, stop, { capture: true, passive: true });
  return stop;
}

interface UseScrollTargetOptions {
  prepareTarget?: (target: WorkspaceFocusTarget) => boolean;
}

/**
 * Hook that watches for scroll target events and scrolls to the matching element.
 * Each element that should be scrollable must have an id matching the pattern:
 * - Segments: id="segment-E1S01"
 * - Characters: id="character-林克"
 * - Clues: id="clue-玉佩"
 *
 * When a scroll target is triggered via `useAppStore.triggerScrollTo()`,
 * this hook retries until the target element is mounted, then scrolls it into
 * view and applies a workspace flash animation.
 */
export function useScrollTarget(
  type: string,
  options?: UseScrollTargetOptions,
): void {
  const scrollTarget = useAppStore((s) => s.scrollTarget);
  const clearScrollTarget = useAppStore((s) => s.clearScrollTarget);
  const pushToast = useAppStore((s) => s.pushToast);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const highlightedElementRef = useRef<HTMLElement | null>(null);
  const stopSettleRef = useRef<(() => void) | null>(null);
  const prepareTarget = options?.prepareTarget;

  useEffect(() => {
    return () => {
      stopSettleRef.current?.();
      stopSettleRef.current = null;
      if (highlightedElementRef.current) {
        highlightedElementRef.current.classList.remove("workspace-focus-flash");
        highlightedElementRef.current = null;
      }
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
      if (highlightTimerRef.current) {
        clearTimeout(highlightTimerRef.current);
        highlightTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!scrollTarget || scrollTarget.type !== type) return;
    const requestId = scrollTarget.request_id;
    const elementId = `${type}-${scrollTarget.id}`;
    let cancelled = false;
    let prepared = false;

    if (retryTimerRef.current) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
    if (highlightTimerRef.current) {
      clearTimeout(highlightTimerRef.current);
      highlightTimerRef.current = null;
    }
    if (highlightedElementRef.current) {
      highlightedElementRef.current.classList.remove("workspace-focus-flash");
      highlightedElementRef.current = null;
    }

    const tryResolveTarget = () => {
      if (cancelled) return;
      const currentTarget = useAppStore.getState().scrollTarget;
      if (!currentTarget || currentTarget.request_id !== requestId) return;

      const el = document.getElementById(elementId);
      if (!el) {
        if (!prepared && prepareTarget) {
          prepared = prepareTarget(currentTarget);
        }
        if (Date.now() >= currentTarget.expires_at) {
          clearScrollTarget(requestId);
          pushToast(i18n.t("errors:scroll_target_not_found", { id: currentTarget.id }), "warning");
          return;
        }
        retryTimerRef.current = setTimeout(tryResolveTarget, 50);
        return;
      }

      stopSettleRef.current?.();
      stopSettleRef.current = scrollIntoViewUntilSettled(el);

      if (currentTarget.highlight) {
        el.classList.remove("workspace-focus-flash");
        void el.getBoundingClientRect();
        el.classList.add("workspace-focus-flash");
        highlightedElementRef.current = el;
        highlightTimerRef.current = setTimeout(() => {
          el.classList.remove("workspace-focus-flash");
          if (highlightedElementRef.current === el) {
            highlightedElementRef.current = null;
          }
          highlightTimerRef.current = null;
        }, 2400);
      }

      clearScrollTarget(requestId);
    };

    tryResolveTarget();

    return () => {
      cancelled = true;
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    };
  }, [clearScrollTarget, prepareTarget, pushToast, scrollTarget, type]);
}
