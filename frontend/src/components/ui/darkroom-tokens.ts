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
  "w-full rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[13px] text-text placeholder:text-text-4 transition-colors hover:border-hairline-strong focus:border-accent/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";

export const GHOST_BTN_CLS =
  "inline-flex items-center gap-1.5 rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-1.5 text-[12px] text-text-2 transition-colors hover:border-hairline-strong hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50";
