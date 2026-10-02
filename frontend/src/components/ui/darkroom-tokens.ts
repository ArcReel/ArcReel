import type { CSSProperties } from "react";

// 视觉方向（#2963）：主按钮为纯色 primary，无渐变与光晕
export const ACCENT_BUTTON_STYLE: CSSProperties = {
  color: "oklch(0.14 0 0)",
  background: "var(--primary)",
};

export const CARD_STYLE: CSSProperties = {
  background:
    "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.55), oklch(0.16 0.010 265 / 0.55))",
};

export const INPUT_CLS =
  "w-full rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[13px] text-text placeholder:text-text-4 transition-colors hover:border-hairline-strong focus:border-primary/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50";

const GHOST_BTN_BASE_CLS =
  "inline-flex items-center rounded-[8px] border border-hairline bg-bg-grad-a/55 text-text-2 transition-colors hover:border-hairline-strong hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-50";

export const GHOST_BTN_CLS = `${GHOST_BTN_BASE_CLS} gap-1.5 px-3 py-1.5 text-[12px]`;

export const GHOST_BTN_LG_CLS = `${GHOST_BTN_BASE_CLS} gap-2 px-3.5 py-2 text-[12.5px]`;

// 视觉方向（#2963）：下拉面板为不透明 overlay surface
export const DROPDOWN_PANEL_STYLE: CSSProperties = {
  background: "var(--popover)",
  boxShadow: "var(--shadow-overlay)",
};

const ACCENT_BTN_BASE_CLS =
  "inline-flex items-center rounded-[8px] font-semibold transition-[filter] enabled:hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-50";

export const ACCENT_BTN_CLS = `${ACCENT_BTN_BASE_CLS} gap-2 px-4 py-2 text-[12.5px]`;

export const ACCENT_BTN_SM_CLS = `${ACCENT_BTN_BASE_CLS} gap-1.5 px-3 py-1.5 text-[12px]`;

export const ICON_BTN_CLS =
  "rounded-[5px] p-1 text-text-4 transition-colors enabled:hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-40";

export const ICON_BTN_FILLED_CLS =
  "rounded-[6px] p-1.5 text-text-3 transition-colors enabled:hover:bg-bg-grad-a enabled:hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-40";

const RADIO_CARD_BASE_CLS =
  "relative flex-1 cursor-pointer rounded-[8px] border px-3.5 py-2.5 text-center text-[12.5px] transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-primary";

export function radioCardClass(selected: boolean): string {
  return selected
    ? `${RADIO_CARD_BASE_CLS} border-primary/45 bg-primary-dim text-text shadow-[inset_0_1px_0_oklch(1_0_0_/_0.05),0_0_22px_-10px_var(--color-primary-glow)]`
    : `${RADIO_CARD_BASE_CLS} border-hairline-soft bg-bg-grad-a/40 text-text-2 hover:border-hairline hover:text-text`;
}

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

// 视觉方向（#2963）：组件内的网格与光晕装饰删除，只保留页面底色渐变。
// 原型基座让两个工具返回隐藏样式，调用点不动；正式迁移时删除工具与调用点。
const HIDDEN_DECORATION: CSSProperties = { display: "none" };

export function posterGridStyle(_opts?: { size?: number; maskShape?: string; opacity?: number }): CSSProperties {
  return HIDDEN_DECORATION;
}

export function ambientGlowStyle(_opts?: { at?: string; intensity?: number }): CSSProperties {
  return HIDDEN_DECORATION;
}
