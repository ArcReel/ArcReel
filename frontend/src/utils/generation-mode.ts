
export type GenerationRoute = "storyboard" | "reference_video";

const ROUTES: readonly string[] = ["storyboard", "reference_video"];

export function normalizeRoute(value: unknown): GenerationRoute {
  return ROUTES.includes(value as string) ? (value as GenerationRoute) : "storyboard";
}

export function gridStoryboardEnabled(
  project: { generation_mode?: GenerationRoute | null; grid_storyboard?: boolean } | null | undefined,
): boolean {
  if (!project) return false;
  return normalizeRoute(project.generation_mode) === "storyboard" && project.grid_storyboard === true;
}

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
