import type { GridGeneration } from "@/types/grid";
import type { ProjectData } from "@/types/project";
import { previewAspect } from "@/utils/preview-aspect";

export type GridImageAspect = "16:9" | "9:16" | "4:3" | "3:4";

/**
 * 联合图的整图比例，与后端 grid_aspect_ratio_for 同口径：方形档取视频比例的规范朝向，
 * 存量 3×2 / 2×3 记录取 4:3 / 3:4。记录未冻结视频比例时按项目画幅回退。
 */
export function gridImageAspect(
  grid: Pick<GridGeneration, "rows" | "cols" | "video_aspect_ratio">,
  project: Pick<ProjectData, "aspect_ratio" | "content_mode"> | null | undefined,
): GridImageAspect {
  const [w, h] = (grid.video_aspect_ratio ?? previewAspect(project)).split(":").map(Number);
  const videoAspect = w > h ? "16:9" : "9:16";
  if (grid.rows === 3 && grid.cols === 2) return "4:3";
  if (grid.rows === 2 && grid.cols === 3) return "3:4";
  return videoAspect;
}
