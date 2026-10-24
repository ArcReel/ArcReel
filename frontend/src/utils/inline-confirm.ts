import type { FocusEvent, KeyboardEvent, PointerEvent } from "react";

// 行内确认区域的退出事件（docs/standards/frontend.md「行内确认」）。打开时聚焦哪个按钮、交还焦点给谁由调用方决定。

type Dismiss = (returnFocus: boolean) => void;

/** 绑在区域内的按钮上：容器不是交互元素，jsx-a11y 不允许在它上面挂键盘事件；焦点一离开容器确认态就结束，Esc 不会落在容器外。 */
export function dismissOnEscape(event: KeyboardEvent, dismiss: Dismiss) {
  if (event.key !== "Escape") return;
  // 不让 Esc 继续冒泡关掉所在的悬浮层
  event.stopPropagation();
  dismiss(true);
}

/** 焦点离开区域即退出，焦点留在用户去的地方。 */
export function dismissOnFocusLeave(event: FocusEvent<HTMLElement>, dismiss: Dismiss) {
  const next = event.relatedTarget;
  // 焦点仍在区域内，或整个窗口失焦（relatedTarget 为空且文档已不持有焦点）：确认态保留
  if (next instanceof Node ? event.currentTarget.contains(next) : !document.hasFocus()) return;
  dismiss(false);
}

/** 点区域内的提示文字不让焦点落到 body：按钮（含按钮内的图标、文字节点）照常接收焦点。 */
export function keepFocusInside(event: PointerEvent<HTMLElement>) {
  if (event.target instanceof Element && event.target.closest("button")) return;
  event.preventDefault();
}
