/**
 * 生成路线工具 — mirrors lib/project_manager.py。
 *
 * 路线二值 `storyboard | reference_video`，创建时锁定、之后不可更改。宫格是分镜路线内的
 * 装配选项（`grid_storyboard` 布尔），不改变喂给视频模型的输入契约，故不是第三条路线。
 */

export type GenerationRoute = "storyboard" | "reference_video";

const ROUTES: readonly GenerationRoute[] = ["storyboard", "reference_video"];

/** 把项目字段收敛成路线值；缺失或非法一律按 storyboard，与迁移器补写口径一致。 */
export function normalizeRoute(value: unknown): GenerationRoute {
  return typeof value === "string" && (ROUTES as readonly string[]).includes(value)
    ? (value as GenerationRoute)
    : "storyboard";
}

/**
 * 宫格是否生效 — mirrors lib/project_manager.py:grid_storyboard_enabled()。
 * 参考路线上残留的 `grid_storyboard=true` 不激活宫格。
 */
export function gridStoryboardEnabled(
  project: { generation_mode?: string | null; grid_storyboard?: boolean } | null | undefined,
): boolean {
  if (!project) return false;
  return normalizeRoute(project.generation_mode) === "storyboard" && project.grid_storyboard === true;
}
