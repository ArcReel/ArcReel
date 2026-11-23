import type { ProjectData } from "@/types/project";

export type PreviewAspect = "9:16" | "16:9";

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
