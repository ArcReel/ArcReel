import type { KeyboardEvent, RefObject } from "react";
import type { PanelImperativeHandle } from "react-resizable-panels";
import { ResizableHandle } from "@/components/ui/resizable";
import { RESIZE_KEYBOARD_STEP } from "./workspace-layout";

interface WorkspaceResizeHandleProps {
  /** 手柄调整的面板，用来读取当前宽度。 */
  panelRef: RefObject<PanelImperativeHandle | null>;
  /** 面板在手柄的哪一侧：`before` 是左侧的侧栏，向右拖变宽；`after` 是右侧的 Agent 面板，向左拖变宽。 */
  panelSide: "before" | "after";
  /** 按方向键后面板应调到的宽度，由调用方落到面板上并记住。 */
  onKeyboardResize: (width: number) => void;
  label: string;
  disabled?: boolean;
  hidden?: boolean;
}

/**
 * 工作区的调宽手柄：指针拖动与双击复位由面板库处理（复位到面板的 defaultSize）；方向键每次移动 16px，
 * 库默认按面板组宽度的 5%。
 */
export function WorkspaceResizeHandle({
  panelRef,
  panelSide,
  onKeyboardResize,
  label,
  disabled,
  hidden,
}: WorkspaceResizeHandleProps) {
  // 库在手柄元素上直接监听 keydown 并先检查 defaultPrevented；React 的捕获阶段处理器先于它执行。
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
