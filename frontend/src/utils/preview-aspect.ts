import type { ProjectData } from "@/types/project";

export type PreviewAspect = "9:16" | "16:9";

/**
 * 分镜时间线与剪辑视图的预览画幅，只分竖屏、横屏两档，3:4、1:1 等回退为横屏。
 * 项目未设置画幅时，narration 与 ad 按竖屏，其余按横屏，与后端 ScriptGenerator._resolve_aspect_ratio 同口径。
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
