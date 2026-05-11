import type { CSSProperties, ReactNode } from "react";
import { ModalShell } from "./ModalShell";

export type GlassHairlineTone = "accent" | "warm";

interface GlassModalProps {
  open: boolean;
  onClose: () => void;
  /** dialog title 节点 id（绑定 aria-labelledby） */
  labelledBy?: string;
  /** dialog description 节点 id（绑定 aria-describedby） */
  describedBy?: string;
  /** 无可视标题时的 aria-label 回退 */
  ariaLabel?: string;
  /** Tailwind 宽度类，默认 w-full max-w-md */
  widthClassName?: string;
  /** 顶部 hairline 渐变线色调，默认 accent；warning 类弹窗用 warm */
  hairlineTone?: GlassHairlineTone;
  /** 点击 backdrop 是否关闭，默认 true */
  closeOnBackdrop?: boolean;
  /** Esc 关闭，默认 true */
  closeOnEscape?: boolean;
  /** 追加到 panel wrapper 的 className（例如 rounded-2xl / max-h-[80vh] 等） */
  panelClassName?: string;
  /** 追加到 panel wrapper 的 inline style */
  panelStyle?: CSSProperties;
  children: ReactNode;
}

// 玻璃面板 Modal — Layer 2 玻璃皮肤，消费 ModalShell primitive，
// 在内部加 PANEL_BG + 顶部 hairline + 圆角。所有 v3 弹窗（含 ConfirmDialog / ConflictDialog）
// 都迁到这里。如需 popover 形态（锚定位）请用 GlassPopover。
export function GlassModal({
  open,
  onClose,
  labelledBy,
  describedBy,
  ariaLabel,
  widthClassName = "w-full max-w-md",
  hairlineTone = "accent",
  closeOnBackdrop = true,
  closeOnEscape = true,
  panelClassName = "",
  panelStyle,
  children,
}: GlassModalProps) {
  return (
    <ModalShell
      open={open}
      onClose={onClose}
      labelledBy={labelledBy}
      describedBy={describedBy}
      ariaLabel={ariaLabel}
      closeOnBackdrop={closeOnBackdrop}
      closeOnEscape={closeOnEscape}
      className={`arc-glass-panel overflow-hidden rounded-2xl ${widthClassName} ${panelClassName}`.trim()}
      style={panelStyle}
    >
      <span aria-hidden="true" className="arc-glass-hairline" data-tone={hairlineTone} />
      {children}
    </ModalShell>
  );
}
