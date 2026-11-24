import type { GenerationRoute } from "@/utils/generation-mode";

export const ROUTE_META: Record<GenerationRoute, { nameKey: string; descKey: string; tag: string }> = {
  storyboard: { nameKey: "route_storyboard", descKey: "route_storyboard_desc", tag: "I2V" },
  reference_video: { nameKey: "route_reference_video", descKey: "route_reference_video_desc", tag: "R2V" },
};
