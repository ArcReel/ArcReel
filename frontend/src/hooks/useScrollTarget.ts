import { useEffect, useRef } from "react";
import { useAppStore } from "@/stores/app-store";

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
export function useScrollTarget(type: string): void {
  const scrollTarget = useAppStore((s) => s.scrollTarget);
  const clearScrollTarget = useAppStore((s) => s.clearScrollTarget);
  const pushToast = useAppStore((s) => s.pushToast);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const highlightedElementRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    return () => {
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
        if (Date.now() >= currentTarget.expires_at) {
          clearScrollTarget(requestId);
          pushToast(`未找到可定位的内容：${currentTarget.id}`, "warning");
          return;
        }
        retryTimerRef.current = setTimeout(tryResolveTarget, 50);
        return;
      }

      el.scrollIntoView({ behavior: "smooth", block: "center" });

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
  }, [scrollTarget, type, clearScrollTarget, pushToast]);
}
