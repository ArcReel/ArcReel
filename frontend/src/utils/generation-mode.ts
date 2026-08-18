/**
 * 生成路线工具 — mirrors lib/project_manager.py。
 *
 * 路线二值 `storyboard | reference_video`，创建时锁定、之后不可更改。宫格是分镜路线内的
 * 装配选项（`grid_storyboard` 布尔），不改变喂给视频模型的输入契约，故不是第三条路线。
 */

export type GenerationRoute = "storyboard" | "reference_video";

const ROUTES: readonly string[] = ["storyboard", "reference_video"];

/**
 * 把未类型化的项目字段解析成路线值。
 *
 * 这是 JSON 边界的解析函数，不是旧值的读时映射：路线必填，落盘值恒为二值之一，
 * 解析不出路线只发生在项目数据未加载或磁盘文件被外部改坏时，此时按 storyboard 呈现，
 * 让页面可用而不是崩在取文案上。
 */
export function normalizeRoute(value: unknown): GenerationRoute {
  return ROUTES.includes(value as string) ? (value as GenerationRoute) : "storyboard";
}

/**
 * 宫格是否生效 — mirrors lib/project_manager.py:grid_storyboard_enabled()。
 * 参考路线上残留的 `grid_storyboard=true` 不激活宫格。
 */
export function gridStoryboardEnabled(
  project: { generation_mode?: GenerationRoute | null; grid_storyboard?: boolean } | null | undefined,
): boolean {
  if (!project) return false;
  return normalizeRoute(project.generation_mode) === "storyboard" && project.grid_storyboard === true;
}

/**
 * 条目数的文案 key — 名词按生成路线定，与创作类型无关：分镜路线报「分镜数」、
 * 参考路线报「视频单元数」。所有展示条目数的位置读同一份映射，不各自写三元判断。
 *
 * `withStatus` 取带状态后缀的那条（「N 分镜 · 制作中」），否则取裸计数那条。
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
