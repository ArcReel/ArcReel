import type { CSSProperties, ReactNode } from "react";
import { ModalShell } from "./ModalShell";

export type GlassHairlineTone = "accent" | "warm";

interface GlassModalBaseProps {
  open: boolean;
  onClose: () => void;
  /** dialog description 节点 id（绑定 aria-describedby） */
  describedBy?: string;
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

// 与 ModalShell 同步：必传 labelledBy（dialog title 节点 id）或 ariaLabel 二选一。
type GlassModalA11yProps =
  | { labelledBy: string; ariaLabel?: never }
  | { labelledBy?: never; ariaLabel: string };

export type GlassModalProps = GlassModalBaseProps & GlassModalA11yProps;

// 玻璃面板 Modal — Layer 2 玻璃皮肤，消费 ModalShell primitive，
// 在内部加 PANEL_BG + 顶部 hairline + 圆角。所有 v3 弹窗（含 ConfirmDialog / ConflictDialog）
// 都迁到这里。如需 popover 形态（锚定位）请用 GlassPopover。
export function GlassModal(props: GlassModalProps) {
  const {
    open,
    onClose,
    describedBy,
    widthClassName = "w-full max-w-md",
    hairlineTone = "accent",
    closeOnBackdrop = true,
    closeOnEscape = true,
    panelClassName = "",
    panelStyle,
    children,
  } = props;
  const labelledBy = "labelledBy" in props ? props.labelledBy : undefined;
  const ariaLabel = "ariaLabel" in props ? props.ariaLabel : undefined;
  const shellA11y = labelledBy != null
    ? { labelledBy }
    : { ariaLabel: ariaLabel as string };
  return (
    <ModalShell
      open={open}
      onClose={onClose}
      describedBy={describedBy}
      closeOnBackdrop={closeOnBackdrop}
      closeOnEscape={closeOnEscape}
      className={`arc-glass-panel overflow-hidden rounded-2xl ${widthClassName} ${panelClassName}`.trim()}
      style={panelStyle}
      {...shellA11y}
    >
      <span aria-hidden="true" className="arc-glass-hairline" data-tone={hairlineTone} />
      {children}
    </ModalShell>
  );
}
