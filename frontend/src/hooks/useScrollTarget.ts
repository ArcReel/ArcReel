import { useEffect, useRef } from "react";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import type { WorkspaceFocusTarget } from "@/types";

const SETTLE_MS = 1000;
const USER_SCROLL_EVENTS = ["wheel", "touchstart", "pointerdown", "keydown"] as const;

function scrollParentOf(el: HTMLElement): HTMLElement | null {
  for (let node = el.parentElement; node; node = node.parentElement) {
    const { overflowY } = getComputedStyle(node);
    if (overflowY === "auto" || overflowY === "scroll") return node;
  }
  return null;
}

function scrollIntoViewUntilSettled(el: HTMLElement): () => void {
  const align = () => el.scrollIntoView({ behavior: "smooth", block: "center" });
  align();
  if (typeof ResizeObserver === "undefined") return () => {};
  const observer = new ResizeObserver(align);
  observer.observe(el);
  const scrollParent = scrollParentOf(el);
  if (scrollParent) {
    observer.observe(scrollParent);
    for (const child of scrollParent.children) observer.observe(child);
  }
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

export function useScrollTarget(
  type: string,
  options?: UseScrollTargetOptions,
): void {
  const scrollTarget = useAppStore((s) => s.scrollTarget);
  const clearScrollTarget = useAppStore((s) => s.clearScrollTarget);
  const pushToast = useAppStore((s) => s.pushToast);
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
      if (highlightTimerRef.current) {
        clearTimeout(highlightTimerRef.current);
        highlightTimerRef.current = null;
      }
    };
  }, []);

  // react-doctor-disable-next-line react-doctor/effect-needs-cleanup -- 重试计时器由返回的 cleanup 清理；高亮计时器与定位跟随须活过定位后 clearScrollTarget 引起的重跑，归 hook 生命周期，下次定位时替换、卸载时由上面的 effect 释放
  useEffect(() => {
    if (!scrollTarget || scrollTarget.type !== type) return;
    const requestId = scrollTarget.request_id;
    const elementId = `${type}-${scrollTarget.id}`;
    let cancelled = false;
    let prepared = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;

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
        retryTimer = setTimeout(tryResolveTarget, 50);
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
      clearTimeout(retryTimer);
    };
  }, [clearScrollTarget, prepareTarget, pushToast, scrollTarget, type]);
}
