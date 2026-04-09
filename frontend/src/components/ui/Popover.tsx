import { createPortal } from "react-dom";
import { useAnchoredPopover } from "@/hooks/useAnchoredPopover";
import { UI_LAYERS } from "@/utils/ui-layers";
import type { RefObject, ReactNode, CSSProperties } from "react";

// ---------------------------------------------------------------------------
// Popover — unified popover panel primitive
// ---------------------------------------------------------------------------
// All popover panels must use this component instead of manually composing
// createPortal + useAnchoredPopover. It escapes the parent stacking context
// (e.g., header's backdrop-blur) via portal, ensuring opaque backgrounds
// and unified z-index management.

/** Panel default background color (gray-900 = rgb(17 24 39)) */
export const POPOVER_BG = "rgb(17 24 39)";

type PopoverAlign = "start" | "center" | "end";
type PopoverLayer = keyof typeof UI_LAYERS;

interface PopoverProps {
  open: boolean;
  onClose?: () => void;
  anchorRef: RefObject<HTMLElement | null>;
  children: ReactNode;
  /** Tailwind width class, e.g. "w-72", "w-96" */
  width?: string;
  /** Additional className (appended to the panel root element) */
  className?: string;
  /** Additional inline styles */
  style?: CSSProperties;
  /** Anchor offset (px), default 8 */
  sideOffset?: number;
  /** Alignment, default "end" */
  align?: PopoverAlign;
  /** z-index layer, default "workspacePopover" */
  layer?: PopoverLayer;
  /** Custom background color, default POPOVER_BG */
  backgroundColor?: string;
}

export function Popover({
  open,
  onClose,
  anchorRef,
  children,
  width = "w-72",
  className = "",
  style,
  sideOffset = 8,
  align,
  layer = "workspacePopover",
  backgroundColor = POPOVER_BG,
}: PopoverProps) {
  const { panelRef, positionStyle } = useAnchoredPopover({
    open,
    anchorRef,
    onClose,
    sideOffset,
    align,
  });

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div
      ref={panelRef}
      className={`fixed isolate ${width} ${UI_LAYERS[layer]} ${className}`}
      style={{
        ...positionStyle,
        backgroundColor,
        ...style,
      }}
    >
      {children}
    </div>,
    document.body,
  );
}
