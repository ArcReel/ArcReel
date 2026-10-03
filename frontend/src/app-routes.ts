/**
 * 顶层应用路由常量 —— 唯一真相源，`router.tsx` 的 `<Switch>` 路由表与
 * `OnboardingTour` 的主界面判断都从这里取值，避免两处字面量各自维护、悄悄漂移。
 */

export const ROUTE_APP = "/app";
export const ROUTE_APP_PROJECTS = "/app/projects";
export const ROUTE_APP_SETTINGS = "/app/settings";
export const ROUTE_APP_ASSETS = "/app/assets";

/** 全局设置的分区，即地址参数 `section` 的取值，顺序同侧栏；缺省或无法识别时落在 `providers`。 */
export const SETTINGS_SECTIONS = [
  "providers",
  "default-models",
  "endpoints",
  "arcreel-agent",
  "agent-memory",
  "external-agent",
  "access-tokens",
  "market",
  "usage",
  "general",
  "prompt-templates",
  "about",
] as const;
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number];

/** 全局设置某个分区的地址；`params` 是该分区自己的定位参数，如使用记录的 `record`。 */
export function settingsSectionPath(section: SettingsSection, params: Record<string, string> = {}): string {
  return `${ROUTE_APP_SETTINGS}?${new URLSearchParams({ section, ...params }).toString()}`;
}

/**
 * 「供应商」分区里要定位的对象，对应的地址参数：
 * - `{ preset }` → `provider=<预置供应商 id>`；
 * - `{ custom, model? }` → `custom=<自定义供应商 id>`，带 `model=<模型 ID>` 时展开并定位到这个模型；
 * - `{ newCustom }` → `custom=new`，可带 `endpoint=<端点 key>` 与 `base_url=<接口地址>` 预填新建表单
 *   （调用端点的「新建供应商并使用」）。
 */
export type ProviderTarget =
  | { preset: string }
  | { custom: number; model?: string }
  | { newCustom: { endpoint?: string; baseUrl?: string } };

/** 全局设置「供应商」分区中某个供应商（或某个自定义模型）的地址。 */
export function providerSettingsPath(target: ProviderTarget): string {
  if ("preset" in target) return settingsSectionPath("providers", { provider: target.preset });
  if ("custom" in target) {
    const params: Record<string, string> = { custom: String(target.custom) };
    if (target.model) params.model = target.model;
    return settingsSectionPath("providers", params);
  }
  const params: Record<string, string> = { custom: "new" };
  if (target.newCustom.endpoint) params.endpoint = target.newCustom.endpoint;
  if (target.newCustom.baseUrl) params.base_url = target.newCustom.baseUrl;
  return settingsSectionPath("providers", params);
}

/**
 * 全局设置「调用端点」分区中某个端点的地址：`endpoint=<端点 key>`。
 * 从自定义供应商跳来时传 `fromCustomProvider`，写成 `from=<自定义供应商 id>`：端点页据此在顶部显示
 * 「返回『供应商名』」，返回地址是 `providerSettingsPath({ custom: from })`。
 */
export function endpointSettingsPath(endpointKey?: string, options: { fromCustomProvider?: number } = {}): string {
  const params: Record<string, string> = {};
  if (endpointKey) params.endpoint = endpointKey;
  if (options.fromCustomProvider !== undefined) params.from = String(options.fromCustomProvider);
  return settingsSectionPath("endpoints", params);
}

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
export const WORKSPACE_ROUTE_CHARACTERS = "characters";
export const WORKSPACE_ROUTE_SCENES = "scenes";
export const WORKSPACE_ROUTE_PROPS = "props";
export const WORKSPACE_ROUTE_PRODUCTS = "products";
export const WORKSPACE_ROUTE_EPISODES = "episodes";

/** 集页的视图查询参数：`?view=edit` 打开剪辑视图，缺省为分镜视图；`tl` 指定打开哪条剪辑时间线。 */
export const EPISODE_VIEW_PARAM = "view";
export const EPISODE_VIEW_EDIT = "edit";
export const EPISODE_VIEW_TIMELINE_PARAM = "tl";

/** 项目工作区内打开某集（集 ID）剪辑视图的相对路径；给出 `timelineId` 时切到那条剪辑时间线。 */
export function episodeEditViewPath(episode: number, timelineId?: string): string {
  const query = new URLSearchParams({ [EPISODE_VIEW_PARAM]: EPISODE_VIEW_EDIT });
  if (timelineId) query.set(EPISODE_VIEW_TIMELINE_PARAM, timelineId);
  return `/${WORKSPACE_ROUTE_EPISODES}/${episode}?${query.toString()}`;
}

/** 无子路径、直接匹配的工作区叶子路由段。`episodes` 除了「分集」视图本身还接受 `/:episodeId`（集页），
 *  在下面的正则里额外拼一条 `episodes/[^/]+` 分支覆盖后者。 */
const WORKSPACE_STATIC_LEAF_ROUTES = [
  WORKSPACE_ROUTE_SETTINGS,
  WORKSPACE_ROUTE_LOREBOOK,
  WORKSPACE_ROUTE_CLUES,
  WORKSPACE_ROUTE_EPISODES,
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
  `^${ROUTE_APP_PROJECTS}/[^/]+(/(?:${WORKSPACE_STATIC_LEAF_ROUTES.join("|")}|${WORKSPACE_ROUTE_EPISODES}/[^/]+))?$`,
  "i",
);
