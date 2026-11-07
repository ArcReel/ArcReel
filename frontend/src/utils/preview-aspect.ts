import type { ProjectData } from "@/types/project";

export type PreviewAspect = "9:16" | "16:9";

/**
 * Preview aspect ratios in the storyboard timeline and editing view have only portrait and landscape categories; 3:4, 1:1, and other ratios fall back to landscape.
 * When no project ratio is set, narration and ad use portrait and other modes use landscape, matching backend ScriptGenerator._resolve_aspect_ratio.
 */
export function previewAspect(
  project: Pick<ProjectData, "aspect_ratio" | "content_mode"> | null | undefined,
): PreviewAspect {
  const contentMode = project?.content_mode ?? "narration";
  const raw =
    typeof project?.aspect_ratio === "string"
      ? project.aspect_ratio
      : (project?.aspect_ratio?.storyboard ??
        (contentMode === "narration" || contentMode === "ad" ? "9:16" : "16:9"));
  return raw === "9:16" ? "9:16" : "16:9";
}
