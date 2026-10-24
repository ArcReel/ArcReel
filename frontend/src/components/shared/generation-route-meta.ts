import type { GenerationRoute } from "@/utils/generation-mode";

/**
 * 生成模式的文案与输入契约标签。向导二卡与设置页只读展示共用同一份，
 * 避免两处各自维护「生成模式 → 名称 / 描述 / I2V-R2V」的对应关系而漂移。
 */
export const ROUTE_META: Record<GenerationRoute, { nameKey: string; descKey: string; tag: string }> = {
  storyboard: { nameKey: "route_storyboard", descKey: "route_storyboard_desc", tag: "I2V" },
  reference_video: { nameKey: "route_reference_video", descKey: "route_reference_video_desc", tag: "R2V" },
};
