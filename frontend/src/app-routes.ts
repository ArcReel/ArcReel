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
 * `/app/projects/:projectName` 下的路由段常量——`router.tsx`（项目设置页）与
 * `StudioCanvasRouter`（内层 `<Switch>`）的 `<Route path>` 都从这里取值，
 * `APP_PROJECT_WORKSPACE_PATTERN` 同样由它们拼出，新增/改名路由只需改这一处。
 */
export const WORKSPACE_ROUTE_SETTINGS = "settings";
export const WORKSPACE_ROUTE_LOREBOOK = "lorebook";
export const WORKSPACE_ROUTE_CLUES = "clues";
export const WORKSPACE_ROUTE_SOURCE = "source";
export const WORKSPACE_ROUTE_CHARACTERS = "characters";
export const WORKSPACE_ROUTE_SCENES = "scenes";
export const WORKSPACE_ROUTE_PROPS = "props";
export const WORKSPACE_ROUTE_PRODUCTS = "products";
export const WORKSPACE_ROUTE_EPISODES = "episodes";

/** 无子路径、直接匹配的工作区叶子路由段。`source` 除了列表页本身还接受 `/:filename`，
 *  在下面的正则里额外拼一条 `source/[^/]+` 分支覆盖后者。 */
const WORKSPACE_STATIC_LEAF_ROUTES = [
  WORKSPACE_ROUTE_SETTINGS,
  WORKSPACE_ROUTE_LOREBOOK,
  WORKSPACE_ROUTE_CLUES,
  WORKSPACE_ROUTE_SOURCE,
  WORKSPACE_ROUTE_CHARACTERS,
  WORKSPACE_ROUTE_SCENES,
  WORKSPACE_ROUTE_PROPS,
  WORKSPACE_ROUTE_PRODUCTS,
] as const;

/**
 * `/app/projects/:projectName` 下真正有路由承接的子路径——`.../settings`
 * 是 router.tsx 里独立注册的 `ProjectSettingsPage` 全屏路由；其余是
 * `StudioCanvasRouter`（nest 路由）内层 `<Switch>` 实际注册的路由集合。
 * 内层没有兜底 404，未匹配的子路径只会渲染空白画布，因此不能整段
 * `/app/projects/` 前缀放行，需要按这份路由表精确匹配。
 * wouter 底层 regexparam 编译路由时带 `i` 标志（大小写不敏感），这里同步加
 * 上 `i`，否则大小写变体的合法路径会被本模式误判为未注册子路径。
 */
export const APP_PROJECT_WORKSPACE_PATTERN = new RegExp(
  `^${ROUTE_APP_PROJECTS}/[^/]+(/(?:${WORKSPACE_STATIC_LEAF_ROUTES.join("|")}|${WORKSPACE_ROUTE_SOURCE}/[^/]+|${WORKSPACE_ROUTE_EPISODES}/[^/]+))?$`,
  "i",
);
