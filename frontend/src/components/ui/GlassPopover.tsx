import { Popover } from "./Popover";
import type { ComponentProps, ReactNode } from "react";
import type { GlassHairlineTone } from "./GlassModal";

interface GlassPopoverProps extends Omit<ComponentProps<typeof Popover>, "backgroundColor"> {
  /** 顶部 hairline 渐变线色调，默认 accent；warning/暖系用 warm */
  hairlineTone?: GlassHairlineTone;
  /** 是否显示顶部 hairline，默认 true */
  showHairline?: boolean;
  children: ReactNode;
}

// GlassPopover — 在 Popover 之上套 PANEL_BG 渐变 + 顶部 hairline + 圆角。
// 之前 TaskHud / UsageDrawer / WorkspaceNotificationsDrawer / ExportScopeDialog 等都各自
// 手动 `<Popover backgroundColor="transparent" style={{ background: PANEL_BG, ... }}>`，
// 现在统一收拢到这里。
export function GlassPopover({
  hairlineTone = "accent",
  showHairline = true,
  className = "",
  children,
  ...rest
}: GlassPopoverProps) {
  return (
    <Popover
      {...rest}
      backgroundColor="transparent"
      className={`arc-glass-panel relative overflow-hidden rounded-lg ${className}`.trim()}
    >
      {showHairline && (
        <span aria-hidden="true" className="arc-glass-hairline" data-tone={hairlineTone} />
      )}
      {children}
    </Popover>
  );
}
