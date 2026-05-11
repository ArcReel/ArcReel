import {
  useEffect,
  useLayoutEffect,
  useRef,
  type CSSProperties,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { useEscapeClose } from "@/hooks/useEscapeClose";
import { useFocusTrap } from "@/hooks/useFocusTrap";

// ModalShell — 站内所有居中模态对话框的通用 primitive。
// 只负责：fixed inset 布局 + portal 出 body + role=dialog + focus trap + escape 关闭 +
// 可点 backdrop。视觉皮肤（玻璃 PANEL_BG / hairline / 圆角）由消费者在 children 容器里
// 自己套上去（典型消费者：GlassModal）。

interface ModalShellBaseProps {
  open: boolean;
  onClose: () => void;
  /** dialog 描述节点 id，绑定 aria-describedby */
  describedBy?: string;
  /** 点击 backdrop 是否关闭，默认 true。设为 false 时仅 Esc 与显式 onClose 关闭。 */
  closeOnBackdrop?: boolean;
  /** 启用 Esc 关闭，默认 true。loading 态可以传 false 防误触。 */
  closeOnEscape?: boolean;
  /** 容器额外 className，追加到 role=dialog 节点 */
  className?: string;
  /** 容器额外 inline style */
  style?: CSSProperties;
  /** Backdrop（黑底 + blur）的自定义样式，覆盖默认 oklch(0 0 0 / 0.65) + blur(2px) */
  backdropStyle?: CSSProperties;
  children: ReactNode;
}

// 类型层强制提供 accessible name：必传 labelledBy（dialog 标题节点 id）或 ariaLabel
// 二选一，避免遗漏导致渲染出无名 role="dialog"。
type ModalShellA11yProps =
  | { labelledBy: string; ariaLabel?: never }
  | { labelledBy?: never; ariaLabel: string };

export type ModalShellProps = ModalShellBaseProps & ModalShellA11yProps;

const DEFAULT_BACKDROP_STYLE: CSSProperties = {
  background: "oklch(0 0 0 / 0.65)",
  backdropFilter: "blur(2px)",
  WebkitBackdropFilter: "blur(2px)",
};

// 模块级 body overflow 引用计数：避免叠加弹窗时先关掉的实例
// 把 body.overflow 还原成可滚动，而仍打开的实例下背景却能滚。
let bodyLockCount = 0;
let bodyOverflowBeforeLock: string | null = null;

function acquireBodyLock() {
  if (bodyLockCount === 0) {
    bodyOverflowBeforeLock = document.body.style.overflow;
    document.body.style.overflow = "hidden";
  }
  bodyLockCount += 1;
}

function releaseBodyLock() {
  bodyLockCount = Math.max(0, bodyLockCount - 1);
  if (bodyLockCount === 0) {
    document.body.style.overflow = bodyOverflowBeforeLock ?? "";
    bodyOverflowBeforeLock = null;
  }
}

export function ModalShell(props: ModalShellProps) {
  const {
    open,
    onClose,
    describedBy,
    closeOnBackdrop = true,
    closeOnEscape = true,
    className,
    style,
    backdropStyle,
    children,
  } = props;
  // discriminated union 下不能同时解构两者，分开读
  const labelledBy = "labelledBy" in props ? props.labelledBy : undefined;
  const ariaLabel = "ariaLabel" in props ? props.ariaLabel : undefined;
  const dialogRef = useRef<HTMLDivElement>(null);

  // 在 open 从 false → true 的边沿用 useLayoutEffect 抓 document.activeElement。
  // React 的提交后顺序是：layout effects（bottom-up）→ paint → useEffects（bottom-up）。
  // 父组件的 layout effect 跑在所有 useEffect 之前，所以即使子组件（如
  // AssetFormModal）随后在 useEffect 里调 nameRef.current?.focus()，此刻
  // activeElement 仍是 modal 打开前真正的触发节点。直接读 useEffect 会拿到
  // 已被子组件抢焦的输入框，关闭后 focus 一个即将卸载的节点 → 焦点丢到 body。
  const returnTargetRef = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (!open) return;
    returnTargetRef.current = (document.activeElement as HTMLElement | null) ?? null;
  }, [open]);

  useEscapeClose(onClose, open && closeOnEscape);
  useFocusTrap(
    dialogRef as RefObject<HTMLElement | null>,
    open,
    returnTargetRef,
  );

  useEffect(() => {
    if (!open) return;
    acquireBodyLock();
    return releaseBodyLock;
  }, [open]);

  if (!open) return null;

  const composedClassName = [
    "relative max-w-[96vw] outline-none",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center px-4">
      <div
        data-testid="modal-backdrop"
        aria-hidden="true"
        onClick={closeOnBackdrop ? onClose : undefined}
        className={`absolute inset-0 ${closeOnBackdrop ? "cursor-pointer" : "cursor-default"}`}
        style={backdropStyle ?? DEFAULT_BACKDROP_STYLE}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        aria-label={!labelledBy ? ariaLabel : undefined}
        className={composedClassName}
        style={style}
        tabIndex={-1}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
