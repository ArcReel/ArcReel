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
 * this hook scrolls the element into view and optionally applies a brief
 * indigo highlight ring that fades after 2 seconds.
 */
export function useScrollTarget(type: string): void {
  const scrollTarget = useAppStore((s) => s.scrollTarget);
  const clearScrollTarget = useAppStore((s) => s.clearScrollTarget);
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (highlightTimerRef.current) {
        clearTimeout(highlightTimerRef.current);
        highlightTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!scrollTarget || scrollTarget.type !== type) return;

    const elementId = `${type}-${scrollTarget.id}`;
    const el = document.getElementById(elementId);
    if (!el) {
      clearScrollTarget();
      return;
    }

    // Smooth scroll to element
    el.scrollIntoView({ behavior: "smooth", block: "center" });

    // Highlight effect
    if (scrollTarget.highlight) {
      el.classList.add("ring-2", "ring-indigo-500");
      el.style.transition = "box-shadow 0.3s ease";
      el.style.boxShadow = "0 0 20px rgba(99, 102, 241, 0.4)";

      if (highlightTimerRef.current) {
        clearTimeout(highlightTimerRef.current);
      }

      highlightTimerRef.current = setTimeout(() => {
        el.classList.remove("ring-2", "ring-indigo-500");
        el.style.boxShadow = "";
        highlightTimerRef.current = null;
      }, 2000);

      clearScrollTarget();
      return;
    }

    clearScrollTarget();
  }, [scrollTarget, type, clearScrollTarget]);
}
