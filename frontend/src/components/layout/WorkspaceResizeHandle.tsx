import type { KeyboardEvent, RefObject } from "react";
import type { PanelImperativeHandle } from "react-resizable-panels";
import { ResizableHandle } from "@/components/ui/resizable";
import { RESIZE_KEYBOARD_STEP } from "./workspace-layout";

interface WorkspaceResizeHandleProps {
  panelRef: RefObject<PanelImperativeHandle | null>;
  panelSide: "before" | "after";
  onKeyboardResize: (width: number) => void;
  label: string;
  disabled?: boolean;
  hidden?: boolean;
}

export function WorkspaceResizeHandle({
  panelRef,
  panelSide,
  onKeyboardResize,
  label,
  disabled,
  hidden,
}: WorkspaceResizeHandleProps) {
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
