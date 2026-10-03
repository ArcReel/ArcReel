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

/** 市场分区的 Tab；「浏览」是默认 Tab，地址里不写。 */
export type MarketTab = "browse" | "shared" | "settings";

/** 全局设置「市场」分区某个 Tab 的地址（`tab=`）。 */
export function marketSettingsPath(tab: MarketTab = "browse"): string {
  return settingsSectionPath("market", tab === "browse" ? {} : { tab });
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

/** 项目设置的分页，即地址参数 `tab` 的取值，顺序同侧栏；缺省或无法识别时落在 `basics`。 */
export const PROJECT_SETTINGS_TABS = ["basics", "style", "models", "voice", "memory", "agent"] as const;
export type ProjectSettingsTab = (typeof PROJECT_SETTINGS_TABS)[number];

/**
 * 项目设置某个分页的地址（顶层路由的绝对路径）。不传 `tab` 时落在默认的「基础」。
 * 在项目工作区的嵌套路由里跳转时在前面加 `~`。
 */
export function projectSettingsPath(projectName: string, tab?: ProjectSettingsTab): string {
  const path = `${ROUTE_APP_PROJECTS}/${encodeURIComponent(projectName)}/${WORKSPACE_ROUTE_SETTINGS}`;
  return tab ? `${path}?${new URLSearchParams({ tab }).toString()}` : path;
}

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
 * 内层没有兜底路由，未匹配的子路径由 router.tsx 按这份路由表拦下、显示 404，
 * 因此不能整段 `/app/projects/` 前缀放行，需要精确匹配。
 * wouter 底层 regexparam 编译路由时带 `i` 标志（大小写不敏感），这里同步加
 * 上 `i`，否则大小写变体的合法路径会被本模式误判为未注册子路径。
 */
export const APP_PROJECT_WORKSPACE_PATTERN = new RegExp(
  `^${ROUTE_APP_PROJECTS}/[^/]+(/(?:${WORKSPACE_STATIC_LEAF_ROUTES.join("|")}|${WORKSPACE_ROUTE_EPISODES}/[^/]+))?$`,
  "i",
);

/** 地址是否落在项目工作区已注册的路由上；与 wouter 一样容忍末尾斜杠。 */
export function isWorkspacePath(location: string): boolean {
  return APP_PROJECT_WORKSPACE_PATTERN.test(location.replace(/(.)\/$/, "$1"));
}
