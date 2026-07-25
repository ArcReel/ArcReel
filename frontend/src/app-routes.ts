/**
 * 顶层应用路由常量 —— 唯一真相源，`router.tsx` 的 `<Switch>` 路由表与
 * `OnboardingTour` 的主界面判断都从这里取值，避免两处字面量各自维护、悄悄漂移。
 */

export const ROUTE_APP = "/app";
export const ROUTE_APP_PROJECTS = "/app/projects";
export const ROUTE_APP_SETTINGS = "/app/settings";
export const ROUTE_APP_ASSETS = "/app/assets";

/** 无子路由的单页顶层路由——精确匹配，前缀不算数。 */
export const APP_TOP_LEVEL_ROUTES = [ROUTE_APP, ROUTE_APP_PROJECTS, ROUTE_APP_SETTINGS, ROUTE_APP_ASSETS] as const;

/**
 * `/app/projects/:projectName` 下真正有路由承接的子路径——`.../settings`
 * 是 router.tsx 里独立注册的 `ProjectSettingsPage` 全屏路由；其余是
 * `StudioCanvasRouter`（nest 路由）内层 `<Switch>` 实际注册的路由集合。
 * 内层没有兜底 404，未匹配的子路径只会渲染空白画布，因此不能整段
 * `/app/projects/` 前缀放行，需要按这份路由表精确匹配。
 */
export const APP_PROJECT_WORKSPACE_PATTERN = new RegExp(
  `^${ROUTE_APP_PROJECTS}/[^/]+(/(?:settings|lorebook|clues|source(?:/[^/]+)?|characters|scenes|props|products|episodes/[^/]+))?$`,
);
