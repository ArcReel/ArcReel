
export const ROUTE_APP = "/app";
export const ROUTE_APP_PROJECTS = "/app/projects";
export const ROUTE_APP_SETTINGS = "/app/settings";
export const ROUTE_APP_ASSETS = "/app/assets";

export const SETTINGS_SECTIONS = [
  "providers",
  "default-models",
  "endpoints",
  "asset-library",
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

export function settingsSectionPath(section: SettingsSection, params: Record<string, string> = {}): string {
  return `${ROUTE_APP_SETTINGS}?${new URLSearchParams({ section, ...params }).toString()}`;
}

export type MarketTab = "browse" | "shared" | "settings";

export const MARKET_MEDIA_TYPES = ["image", "video"] as const;
export type MarketMediaType = (typeof MARKET_MEDIA_TYPES)[number];

export function parseMarketMedia(value: string | null | undefined): MarketMediaType | null {
  return MARKET_MEDIA_TYPES.find((media) => media === value) ?? null;
}

export function marketSettingsPath(tab: MarketTab = "browse", options: { media?: string | null } = {}): string {
  const params: Record<string, string> = tab === "browse" ? {} : { tab };
  const media = parseMarketMedia(options.media);
  if (media) params.media = media;
  return settingsSectionPath("market", params);
}

export type ProviderTarget =
  | { preset: string; model?: string }
  | { custom: number; model?: string }
  | { newCustom: { endpoint?: string; baseUrl?: string } };

export function providerSettingsPath(target: ProviderTarget): string {
  if ("preset" in target) {
    const params: Record<string, string> = { provider: target.preset };
    if (target.model) params.model = target.model;
    return settingsSectionPath("providers", params);
  }
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

export function endpointSettingsPath(endpointKey?: string, options: { fromCustomProvider?: number } = {}): string {
  const params: Record<string, string> = {};
  if (endpointKey) params.endpoint = endpointKey;
  if (options.fromCustomProvider !== undefined) params.from = String(options.fromCustomProvider);
  return settingsSectionPath("endpoints", params);
}

export const APP_TOP_LEVEL_ROUTES = [ROUTE_APP, ROUTE_APP_PROJECTS, ROUTE_APP_SETTINGS, ROUTE_APP_ASSETS] as const;

export const WORKSPACE_ROUTE_SETTINGS = "settings";

export const PROJECT_SETTINGS_TABS = ["basics", "style", "models", "voice", "memory", "agent"] as const;
export type ProjectSettingsTab = (typeof PROJECT_SETTINGS_TABS)[number];

export function projectSettingsPath(
  projectName: string,
  tab?: ProjectSettingsTab,
  params: Record<string, string> = {},
): string {
  const path = `${ROUTE_APP_PROJECTS}/${encodeURIComponent(projectName)}/${WORKSPACE_ROUTE_SETTINGS}`;
  const query = new URLSearchParams({ ...(tab ? { tab } : {}), ...params }).toString();
  return query ? `${path}?${query}` : path;
}

export const WORKSPACE_ROUTE_CHARACTERS = "characters";
export const WORKSPACE_ROUTE_SCENES = "scenes";
export const WORKSPACE_ROUTE_PROPS = "props";
export const WORKSPACE_ROUTE_PRODUCTS = "products";
export const WORKSPACE_ROUTE_EPISODES = "episodes";
export const WORKSPACE_ROUTE_FILES = "files";
export const WORKSPACE_ROUTE_DRAFTS = "drafts";

export const EPISODE_VIEW_PARAM = "view";
export const EPISODE_VIEW_EDIT = "edit";
export const EPISODE_VIEW_TIMELINE_PARAM = "tl";

export function episodeEditViewPath(episode: number, timelineId?: string): string {
  const query = new URLSearchParams({ [EPISODE_VIEW_PARAM]: EPISODE_VIEW_EDIT });
  if (timelineId) query.set(EPISODE_VIEW_TIMELINE_PARAM, timelineId);
  return `/${WORKSPACE_ROUTE_EPISODES}/${episode}?${query.toString()}`;
}

const WORKSPACE_STATIC_LEAF_ROUTES = [
  WORKSPACE_ROUTE_SETTINGS,
  WORKSPACE_ROUTE_EPISODES,
  WORKSPACE_ROUTE_CHARACTERS,
  WORKSPACE_ROUTE_SCENES,
  WORKSPACE_ROUTE_PROPS,
  WORKSPACE_ROUTE_PRODUCTS,
  WORKSPACE_ROUTE_FILES,
  WORKSPACE_ROUTE_DRAFTS,
] as const;

/**
 * `/app/projects/:projectName` 下真正有路由承接的子路径——`.../settings`
 * 是 router.tsx 里独立注册的 `ProjectSettingsPage` 全屏路由；其余是
 * `StudioCanvasRouter`（nest 路由）内层 `<Switch>` 实际注册的路由集合。
 * 内层 `<Switch>` 的兜底路由在画布内显示空状态；新手引导据此判断地址是否落在主界面内，
 * 未注册的子路径不算。
 * wouter 底层 regexparam 编译路由时带 `i` 标志（大小写不敏感），这里同步加
 * 上 `i`，否则大小写变体的合法路径会被本模式误判为未注册子路径。
 */
export const APP_PROJECT_WORKSPACE_PATTERN = new RegExp(
  `^${ROUTE_APP_PROJECTS}/[^/]+(/(?:${WORKSPACE_STATIC_LEAF_ROUTES.join("|")}|${WORKSPACE_ROUTE_EPISODES}/[^/]+))?$`,
  "i",
);
