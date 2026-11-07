import type { KeyboardEvent, RefObject } from "react";
import type { PanelImperativeHandle } from "react-resizable-panels";
import { ResizableHandle } from "@/components/ui/resizable";
import { RESIZE_KEYBOARD_STEP } from "./workspace-layout";

interface WorkspaceResizeHandleProps {
  /** Panel resized by this handle, for reading current widths. */
  panelRef: RefObject<PanelImperativeHandle | null>;
  /** Panel side: `before` is the left sidebar, widened by dragging right; `after` is the right Agent panel, widened by dragging left. */
  panelSide: "before" | "after";
  /** Target width after arrow keys; callers apply it to panels and remember it. */
  onKeyboardResize: (width: number) => void;
  label: string;
  disabled?: boolean;
  hidden?: boolean;
}

/**
 * Workspace resize handles: the panel library handles pointer dragging and double-click reset (to panel defaultSize); arrow keys move 16px each time,
 * while the library defaults to 5% of panel-group width.
 */
export function WorkspaceResizeHandle({
  panelRef,
  panelSide,
  onKeyboardResize,
  label,
  disabled,
  hidden,
}: WorkspaceResizeHandleProps) {
  // The library listens for keydown directly on handles and checks defaultPrevented first; React capture handlers run before it.
  const handleKeyDownCapture = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const panel = panelRef.current;
    if (!panel || disabled) return;
    event.preventDefault();
    const towardsPanel = (event.key === "ArrowLeft") === (panelSide === "before");
    const delta = towardsPanel ? -RESIZE_KEYBOARD_STEP : RESIZE_KEYBOARD_STEP;
    onKeyboardResize(panel.getSize().inPixels + delta);
  };

  return (
    <ResizableHandle
      aria-label={label}
      disabled={disabled}
      hidden={hidden}
      onKeyDownCapture={handleKeyDownCapture}
    />
  );
}
