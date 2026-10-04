import type { CSSProperties } from "react";

export const CARD_STYLE: CSSProperties = {
  background:
    "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.55), oklch(0.16 0.010 265 / 0.55))",
};

export const INPUT_CLS =
  "w-full rounded-md border border-border bg-card/55 px-3 py-2 text-[13px] text-foreground placeholder:text-muted-foreground transition-colors hover:border-input focus:border-primary/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

const GHOST_BTN_BASE_CLS =
  "inline-flex items-center rounded-md border border-border bg-card/55 text-subtle-foreground transition-colors hover:border-input hover:bg-card hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

export const GHOST_BTN_CLS = `${GHOST_BTN_BASE_CLS} gap-1.5 px-3 py-1.5 text-[12px]`;

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
