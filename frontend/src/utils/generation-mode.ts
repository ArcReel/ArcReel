/**
 * Generation-mode helpers, mirroring lib/project/project_manager.py.
 *
 * Binary `storyboard | reference_video` modes lock at creation, immutable afterward. Grids are storyboard image-to-video
 * assembly options (`grid_storyboard` bool), without changing video-model input contracts, never third modes.
 */

export type GenerationRoute = "storyboard" | "reference_video";

const ROUTES: readonly string[] = ["storyboard", "reference_video"];

/**
 * Parse untyped project fields as generation modes.
 *
 * Persisted modes are required and binary. Unloaded/corrupted files fall back to storyboard
 * presentation, keeping pages usable rather than crashing copy lookup.
 */
export function normalizeRoute(value: unknown): GenerationRoute {
  return ROUTES.includes(value as string) ? (value as GenerationRoute) : "storyboard";
}

/**
 * Effective grids, mirroring lib/project/project_manager.py:grid_storyboard_enabled().
 * Residual `grid_storyboard=true` on reference video never activates grids.
 */
export function gridStoryboardEnabled(
  project: { generation_mode?: GenerationRoute | null; grid_storyboard?: boolean } | null | undefined,
): boolean {
  if (!project) return false;
  return normalizeRoute(project.generation_mode) === "storyboard" && project.grid_storyboard === true;
}

/**
 * Entry-count copy keys follow generation modes, independent of content types: storyboards for image-to-video,
 * video units for reference video. All count displays share mappings, never independent ternaries.
 *
 * `withStatus` chooses status-suffixed counts (N storyboards · In production), otherwise bare counts.
 */
const ITEM_COUNT_NOUNS: Record<GenerationRoute, string> = {
  storyboard: "storyboard_count",
  reference_video: "video_unit_count",
};

export function itemCountKey(
  route: GenerationRoute,
  { withStatus = false }: { withStatus?: boolean } = {},
): string {
  const noun = ITEM_COUNT_NOUNS[route];
  return withStatus ? `${noun}_and_status` : noun;
}
