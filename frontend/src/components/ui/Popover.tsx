import { useLayoutEffect } from "react";
import {
  FloatingPortal,
  autoUpdate,
  flip,
  offset,
  shift,
  size,
  useDismiss,
  useFloating,
  useInteractions,
  type Placement,
} from "@floating-ui/react";
import { UI_LAYERS } from "@/utils/ui-layers";
import type { RefObject, ReactNode, CSSProperties } from "react";

// ---------------------------------------------------------------------------
// Popover — 统一弹出面板原语
// ---------------------------------------------------------------------------
// 所有弹出面板必须使用此组件，而非手动组合 FloatingPortal + useFloating 或
// createPortal + 手写定位。它通过 floating-ui + FloatingPortal 脱离父级层叠
// 上下文，统一 flip/shift/外部点击/Esc 处理，保持 z-index 和背景不透明。

/** 面板默认背景色（Darkroom oklch panel tone，与 bg-bg-grad-a 同源） */
export const POPOVER_BG = "oklch(0.182 0.011 270)";

type PopoverAlign = "start" | "center" | "end";
type PopoverLayer = keyof typeof UI_LAYERS;

function alignToPlacement(align: PopoverAlign): Placement {
  if (align === "start") return "bottom-start";
  if (align === "center") return "bottom";
  return "bottom-end";
}

interface PopoverProps {
  open: boolean;
  onClose?: () => void;
  /** ref 式锚点；anchorElement 优先。两者都为空时面板不渲染。 */
  anchorRef?: RefObject<HTMLElement | null>;
  /** element-as-state 锚点，供动态 anchor（如 caret-tracking）使用；优先于 anchorRef。 */
  anchorElement?: HTMLElement | null;
  children: ReactNode;
  /** Tailwind width class, e.g. "w-72", "w-96" */
  width?: string;
  /** 额外 className（追加到面板根元素） */
  className?: string;
  /** 额外内联样式 */
  style?: CSSProperties;
  /** 锚点偏移量（px），默认 8 */
  sideOffset?: number;
  /** 对齐方式，默认 "end"（映射为 bottom-end） */
  align?: PopoverAlign;
  /** 显式 placement；提供时覆盖 align */
  placement?: Placement;
  /** z-index 层级，默认 "workspacePopover" */
  layer?: PopoverLayer;
  /** 自定义背景色，默认 POPOVER_BG */
  backgroundColor?: string;
  /** 传入时启用 size middleware，把面板 max-height 夹到 min(maxHeight, availableHeight) */
  maxHeight?: number;
}

export function Popover({
  open,
  onClose,
  anchorRef,
  anchorElement,
  children,
  width = "w-72",
  className = "",
  style,
  sideOffset = 8,
  align = "end",
  placement,
  layer = "workspacePopover",
  backgroundColor = POPOVER_BG,
  maxHeight,
}: PopoverProps) {
  const { refs, floatingStyles, context } = useFloating({
    open,
    onOpenChange: (nextOpen) => {
      if (!nextOpen) onClose?.();
    },
    strategy: "fixed",
    placement: placement ?? alignToPlacement(align),
    whileElementsMounted: autoUpdate,
    middleware: [
      offset(sideOffset),
      flip({ padding: 12 }),
      shift({ padding: 12 }),
      ...(maxHeight !== undefined
        ? [
            size({
              padding: 8,
              apply({ availableHeight, elements }) {
                elements.floating.style.maxHeight = `${Math.min(maxHeight, availableHeight)}px`;
              },
            }),
          ]
        : []),
    ],
  });
  const dismiss = useDismiss(context, { outsidePress: true, escapeKey: true });
  const { getFloatingProps } = useInteractions([dismiss]);

  // 在布局阶段同步 reference——paint 之前绑定避免首帧错位。
  // 依赖里必须包含 open：常见用法是 <div ref={anchorRef}>…<Popover anchorRef={anchorRef}/></div>
  // （ref 挂父节点），而 React commit phase 是 post-order，子 Popover 的 layout effect
  // 早于父节点的 ref attach，首次执行时 anchorRef.current 仍是 null。若依赖只含
  // 稳定引用，setReference(null) 就被锁死，floating-ui 无 reference 把面板钉在视窗
  // 左上角。把 open 加进依赖后，open: false→true 时 effect 重跑，此时父 ref 早已 attach。
  useLayoutEffect(() => {
    refs.setReference(anchorElement ?? anchorRef?.current ?? null);
  }, [open, anchorElement, anchorRef, refs]);

  if (!open) return null;

  return (
    <FloatingPortal>
      <div
        // `refs.setFloating` is floating-ui 的 stable 回调 ref；react-hooks/refs
        // 规则误认为是读取 ref.current，这里安全。
        // eslint-disable-next-line react-hooks/refs
        ref={refs.setFloating}
        {...getFloatingProps()}
        className={`isolate ${width} ${UI_LAYERS[layer]} ${className}`}
        style={{
          ...floatingStyles,
          backgroundColor,
          ...style,
        }}
      >
        {children}
      </div>
    </FloatingPortal>
  );
}
