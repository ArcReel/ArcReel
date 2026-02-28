import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type RefObject } from "react";

type PopoverAlign = "start" | "center" | "end";

interface UseAnchoredPopoverOptions {
  open: boolean;
  anchorRef: RefObject<HTMLElement | null>;
  onClose?: () => void;
  align?: PopoverAlign;
  sideOffset?: number;
  collisionPadding?: number;
}

interface PopoverPosition {
  top: number;
  left: number;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

export function useAnchoredPopover({
  open,
  anchorRef,
  onClose,
  align = "end",
  sideOffset = 8,
  collisionPadding = 12,
}: UseAnchoredPopoverOptions) {
  const panelRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  const [position, setPosition] = useState<PopoverPosition>({ top: 0, left: 0 });

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useLayoutEffect(() => {
    if (!open || typeof window === "undefined") return;

    const updatePosition = () => {
      const anchor = anchorRef.current;
      const panel = panelRef.current;
      if (!anchor || !panel) return;

      const anchorRect = anchor.getBoundingClientRect();
      const panelRect = panel.getBoundingClientRect();
      const viewportWidth = document.documentElement.clientWidth;
      const top = anchorRect.bottom + sideOffset;

      let desiredLeft = anchorRect.right - panelRect.width;
      if (align === "start") {
        desiredLeft = anchorRect.left;
      } else if (align === "center") {
        desiredLeft = anchorRect.left + (anchorRect.width - panelRect.width) / 2;
      }

      const maxLeft = Math.max(collisionPadding, viewportWidth - panelRect.width - collisionPadding);
      const left = clamp(desiredLeft, collisionPadding, maxLeft);

      setPosition((prev) => {
        if (prev.top === top && prev.left === left) {
          return prev;
        }
        return { top, left };
      });
    };

    updatePosition();
    const frameId = window.requestAnimationFrame(updatePosition);

    const resizeObserver =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => updatePosition());

    if (anchorRef.current) {
      resizeObserver?.observe(anchorRef.current);
    }
    if (panelRef.current) {
      resizeObserver?.observe(panelRef.current);
    }

    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);

    return () => {
      window.cancelAnimationFrame(frameId);
      resizeObserver?.disconnect();
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open, anchorRef, align, sideOffset, collisionPadding]);

  useEffect(() => {
    if (!open || !onCloseRef.current) return;

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;

      const anchor = anchorRef.current;
      const panel = panelRef.current;
      if (anchor?.contains(target) || panel?.contains(target)) {
        return;
      }

      onCloseRef.current?.();
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onCloseRef.current?.();
      }
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, anchorRef]);

  const positionStyle: CSSProperties = {
    top: `${position.top}px`,
    left: `${position.left}px`,
  };

  return {
    panelRef,
    positionStyle,
  };
}
