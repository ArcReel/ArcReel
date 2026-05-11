import type { CSSProperties } from "react";

export const ACCENT_BUTTON_STYLE: CSSProperties = {
  color: "oklch(0.14 0 0)",
  background: "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
  boxShadow:
    "inset 0 1px 0 oklch(1 0 0 / 0.3), 0 0 0 1px oklch(0.55 0.10 295 / 0.4), 0 6px 18px -8px var(--color-accent-glow)",
};

export const CARD_STYLE: CSSProperties = {
  background:
    "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.55), oklch(0.16 0.010 265 / 0.55))",
};

export const INPUT_CLS =
  "w-full rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[13px] text-text placeholder:text-text-4 transition-colors hover:border-hairline-strong focus:border-accent/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50";

const GHOST_BTN_BASE_CLS =
  "inline-flex items-center rounded-[8px] border border-hairline bg-bg-grad-a/55 text-text-2 transition-colors hover:border-hairline-strong hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50";

export const GHOST_BTN_CLS = `${GHOST_BTN_BASE_CLS} gap-1.5 px-3 py-1.5 text-[12px]`;

export const GHOST_BTN_LG_CLS = `${GHOST_BTN_BASE_CLS} gap-2 px-3.5 py-2 text-[12.5px]`;

export const DROPDOWN_PANEL_STYLE: CSSProperties = {
  background:
    "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.92), oklch(0.16 0.010 265 / 0.92))",
  backdropFilter: "blur(12px)",
  WebkitBackdropFilter: "blur(12px)",
};

const ACCENT_BTN_BASE_CLS =
  "inline-flex items-center rounded-[8px] font-semibold transition-transform motion-safe:hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0";

export const ACCENT_BTN_CLS = `${ACCENT_BTN_BASE_CLS} gap-2 px-4 py-2 text-[12.5px]`;

export const ACCENT_BTN_SM_CLS = `${ACCENT_BTN_BASE_CLS} gap-1.5 px-3 py-1.5 text-[12px]`;

export const ICON_BTN_CLS =
  "rounded-[5px] p-1 text-text-4 transition-colors enabled:hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-40";

export const ICON_BTN_FILLED_CLS =
  "rounded-[6px] p-1.5 text-text-3 transition-colors enabled:hover:bg-bg-grad-a enabled:hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-40";

const RADIO_CARD_BASE_CLS =
  "relative flex-1 cursor-pointer rounded-[8px] border px-3.5 py-2.5 text-center text-[12.5px] transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent";

export function radioCardClass(selected: boolean): string {
  return selected
    ? `${RADIO_CARD_BASE_CLS} border-accent/45 bg-accent-dim text-text shadow-[inset_0_1px_0_oklch(1_0_0_/_0.05),0_0_22px_-10px_var(--color-accent-glow)]`
    : `${RADIO_CARD_BASE_CLS} border-hairline-soft bg-bg-grad-a/40 text-text-2 hover:border-hairline hover:text-text`;
}

interface PosterGridOptions {
  size?: number;
  maskShape?: string;
  opacity?: number;
}

export function posterGridStyle(opts?: PosterGridOptions): CSSProperties {
  const size = opts?.size ?? 40;
  const mask = `radial-gradient(${opts?.maskShape ?? "70% 70% at 50% 50%"}, black, transparent)`;
  const style: CSSProperties = {
    backgroundImage:
      "linear-gradient(oklch(1 0 0) 1px, transparent 1px), linear-gradient(90deg, oklch(1 0 0) 1px, transparent 1px)",
    backgroundSize: `${size}px ${size}px`,
    maskImage: mask,
    WebkitMaskImage: mask,
  };
  if (opts?.opacity !== undefined) style.opacity = opts.opacity;
  return style;
}

interface AmbientGlowOptions {
  at?: string;
  intensity?: number;
}

export function ambientGlowStyle(opts?: AmbientGlowOptions): CSSProperties {
  const at = opts?.at ?? "50% 0%";
  const alpha = opts?.intensity ?? 0.16;
  return {
    background: `radial-gradient(circle at ${at}, oklch(0.76 0.09 295 / ${alpha}), transparent 60%)`,
  };
}
