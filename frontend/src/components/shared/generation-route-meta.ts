import type { GenerationRoute } from "@/utils/generation-mode";

/**
 * Generation-mode copy and input-contract labels. Shared by the wizard’s two cards and the read-only display in settings,
 * to prevent drift from separately maintaining the generation-mode-to-name/description/I2V-R2V mapping.
 */
export const ROUTE_META: Record<GenerationRoute, { nameKey: string; descKey: string; tag: string }> = {
  storyboard: { nameKey: "route_storyboard", descKey: "route_storyboard_desc", tag: "I2V" },
  reference_video: { nameKey: "route_reference_video", descKey: "route_reference_video_desc", tag: "R2V" },
};
