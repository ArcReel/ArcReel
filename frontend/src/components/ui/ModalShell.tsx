import {
  useEffect,
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

interface ModalShellProps {
  open: boolean;
  onClose: () => void;
  /** dialog 标题节点 id，绑定 aria-labelledby */
  labelledBy?: string;
  /** dialog 描述节点 id，绑定 aria-describedby */
  describedBy?: string;
  /** dialog 无可视标题时的回退 aria-label */
  ariaLabel?: string;
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

const DEFAULT_BACKDROP_STYLE: CSSProperties = {
  background: "oklch(0 0 0 / 0.65)",
  backdropFilter: "blur(2px)",
  WebkitBackdropFilter: "blur(2px)",
};

export function ModalShell({
  open,
  onClose,
  labelledBy,
  describedBy,
  ariaLabel,
  closeOnBackdrop = true,
  closeOnEscape = true,
  className,
  style,
  backdropStyle,
  children,
}: ModalShellProps) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEscapeClose(onClose, open && closeOnEscape);
  useFocusTrap(
    dialogRef as RefObject<HTMLElement | null>,
    open,
  );

  // 锁 body 滚动：modal 打开时禁止背景滚动（多数 v3 modal 之前没做这件事，体验略糙）
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
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
