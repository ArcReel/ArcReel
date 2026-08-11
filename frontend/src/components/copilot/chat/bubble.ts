import type { CSSProperties } from "react";

// ---------------------------------------------------------------------------
// 消息气泡外壳的共享形态。
//
// 用户消息的只读气泡（ChatMessage）与原地编辑框（MessageRow 的 MessageEditor）
// 逐像素同形——「气泡原地变编辑框，形态不变」，外壳样式因此只在此处定义一份。
// ---------------------------------------------------------------------------

/** 所有气泡共用的圆角与内距。 */
export const BUBBLE_SHELL_CLASS = "rounded-xl px-2.5 py-1.5";

/** 用户气泡的靠右与限宽。 */
export const USER_BUBBLE_LAYOUT_CLASS = "ml-auto max-w-[85%]";

/** 用户气泡的底色与描边。 */
export const USER_BUBBLE_STYLE: CSSProperties = {
  background: "linear-gradient(180deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.06))",
  border: "1px solid var(--color-accent-soft)",
};

/** 气泡内眉题（角色标签 / 编辑态标题），配色由调用方给。 */
export const BUBBLE_LABEL_CLASS = "mb-1 text-[10px] font-semibold uppercase";
export const BUBBLE_LABEL_STYLE: CSSProperties = { letterSpacing: "0.06em" };
