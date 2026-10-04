import type { CSSProperties } from "react";

/**
 * @deprecated 旧主按钮的内联样式，外观已收为 Button 的 default 变体（纯色、无光晕）。
 * 新代码直接用 `components/ui/button`，调用处由各区域逐步替换。
 */
export const ACCENT_BUTTON_STYLE: CSSProperties = {
  color: "var(--primary-foreground)",
  background: "var(--primary)",
};

export const CARD_STYLE: CSSProperties = {
  background:
    "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.55), oklch(0.16 0.010 265 / 0.55))",
};

export const INPUT_CLS =
  "w-full rounded-md border border-border bg-card/55 px-3 py-2 text-[13px] text-foreground placeholder:text-muted-foreground transition-colors hover:border-input focus:border-primary/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

const GHOST_BTN_BASE_CLS =
  "inline-flex items-center rounded-md border border-border bg-card/55 text-subtle-foreground transition-colors hover:border-input hover:bg-card hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

export const GHOST_BTN_CLS = `${GHOST_BTN_BASE_CLS} gap-1.5 px-3 py-1.5 text-[12px]`;

export const GHOST_BTN_LG_CLS = `${GHOST_BTN_BASE_CLS} gap-2 px-3.5 py-2 text-[12.5px]`;

// 背景写在 ACCENT_BUTTON_STYLE 的内联样式里，悬停用亮度变化表达；主按钮不再悬停上移。
const ACCENT_BTN_BASE_CLS =
  "inline-flex items-center rounded-md font-semibold transition-[filter] enabled:hover:brightness-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

export const ACCENT_BTN_CLS = `${ACCENT_BTN_BASE_CLS} gap-2 px-4 py-2 text-[12.5px]`;

/**
 * 由字符串派生一个稳定色相（0-359）。同名同 salt 恒得同色，换名字才换色，
 * 让「没有配图」的资源在整个界面里保持各自固定的身份色。
 */
export function hashHue(name: string, salt: number): number {
  let hash = salt;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return hash % 360;
}
