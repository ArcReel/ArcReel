/**
 * Sole top-level app route constants, shared by `router.tsx` `<Switch>` and
 * `OnboardingTour` main-screen predicates, avoiding independent drifting literals.
 */

export const ROUTE_APP = "/app";
export const ROUTE_APP_PROJECTS = "/app/projects";
export const ROUTE_APP_SETTINGS = "/app/settings";
export const ROUTE_APP_ASSETS = "/app/assets";

/** Global-setting `section` values in sidebar order; absent/unrecognized defaults to `providers`. */
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

/**
 * Global-section paths; `params` supplies section locators such as usage `record`.
 * `SettingsSection` checks names, reporting old links on renames at typecheck.
 * Prefix nested-route navigation with `~`: `` `~${settingsSectionPath("usage")}` ``.
 * Providers/endpoints use `providerSettingsPath` / `endpointSettingsPath`, never handcrafted queries.
 */
export function settingsSectionPath(section: SettingsSection, params: Record<string, string> = {}): string {
  return `${ROUTE_APP_SETTINGS}?${new URLSearchParams({ section, ...params }).toString()}`;
}

/** Market-section tabs; Browse is the default and omitted from URLs. */
export type MarketTab = "browse" | "shared" | "settings";

/** Media-type filters for Market Browse tabs, the values of URL parameter `media`. */
export const MARKET_MEDIA_TYPES = ["image", "video"] as const;
export type MarketMediaType = (typeof MARKET_MEDIA_TYPES)[number];

/** Media types parsed from URL parameter `media`; null when missing/unrecognized. */
export function parseMarketMedia(value: string | null | undefined): MarketMediaType | null {
  return MARKET_MEDIA_TYPES.find((media) => media === value) ?? null;
}

/**
 * Global-settings Market tab URLs (`tab=`).
 * Passing `media` writes `media=<媒体类型>`, presetting Browse media filters; omit media types absent from markets (text/audio).
 */
export function marketSettingsPath(tab: MarketTab = "browse", options: { media?: string | null } = {}): string {
  const params: Record<string, string> = tab === "browse" ? {} : { tab };
  const media = parseMarketMedia(options.media);
  if (media) params.media = media;
  return settingsSectionPath("market", params);
}

/**
 * Targets within Providers sections and corresponding URL parameters:
 * - `{ preset, model? }` → `provider=<预置供应商 id>`;
 * - `{ custom, model? }` → `custom=<自定义供应商 id>`;
 *   either opens model Sheets when carrying `model=<模型 ID>`;
 * - `{ newCustom }` → `custom=new`, optionally `endpoint=<端点 key>` and `base_url=<接口地址>` to prefill new-provider forms
 *   (the endpoint Create provider and use action).
 */
export type ProviderTarget =
  | { preset: string; model?: string }
  | { custom: number; model?: string }
  | { newCustom: { endpoint?: string; baseUrl?: string } };

/** Global-settings URLs for providers (or custom models) in Providers sections. */
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

/**
 * Global-settings endpoint URLs in API endpoints sections: `endpoint=<端点 key>`.
 * Pass `fromCustomProvider` when arriving from custom providers, writing `from=<自定义供应商 id>`: endpoint pages then show
 * Return to provider name above, targeting `providerSettingsPath({ custom: from })`.
 */
export function endpointSettingsPath(endpointKey?: string, options: { fromCustomProvider?: number } = {}): string {
  const params: Record<string, string> = {};
  if (endpointKey) params.endpoint = endpointKey;
  if (options.fromCustomProvider !== undefined) params.from = String(options.fromCustomProvider);
  return settingsSectionPath("endpoints", params);
}

/** Single-page top-level routes without subroutes: exact matches, never prefixes. */
export const APP_TOP_LEVEL_ROUTES = [ROUTE_APP, ROUTE_APP_PROJECTS, ROUTE_APP_SETTINGS, ROUTE_APP_ASSETS] as const;

/**
 * Route-segment constants beneath `/app/projects/:projectName`: `router.tsx` (project settings) and
 * `StudioCanvasRouter` (inner `<Switch>`) both source `<Route path>` values here;
 * `APP_PROJECT_WORKSPACE_PATTERN` also combines them, so adding/renaming routes changes only this location.
 */
export const WORKSPACE_ROUTE_SETTINGS = "settings";

/** Project-settings tabs, URL parameter `tab` values in sidebar order; missing/unrecognized values use `basics`. */
export const PROJECT_SETTINGS_TABS = ["basics", "style", "models", "voice", "memory", "agent"] as const;
export type ProjectSettingsTab = (typeof PROJECT_SETTINGS_TABS)[number];

/**
 * Project-settings tab URLs (absolute top-level paths). Omitting `tab` selects default Basics.
 * `params` contains tab-specific URL parameters, such as selected project-memory files (`file=`).
 * Prefix `~` when navigating from nested project-workspace routes.
 */
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

/** Episode-view query parameters: `?view=edit` opens editing, default storyboard; `tl` selects editing timelines. */
export const EPISODE_VIEW_PARAM = "view";
export const EPISODE_VIEW_EDIT = "edit";
export const EPISODE_VIEW_TIMELINE_PARAM = "tl";

/** Workspace-relative episode-ID editing paths; `timelineId` selects that editing timeline. */
export function episodeEditViewPath(episode: number, timelineId?: string): string {
  const query = new URLSearchParams({ [EPISODE_VIEW_PARAM]: EPISODE_VIEW_EDIT });
  if (timelineId) query.set(EPISODE_VIEW_TIMELINE_PARAM, timelineId);
  return `/${WORKSPACE_ROUTE_EPISODES}/${episode}?${query.toString()}`;
}

/** Directly matched workspace leaf-route segments without subpaths. `episodes` accepts `/:episodeId` (episode pages) as well as episode-list views;
 *  the regex below adds an `episodes/[^/]+` branch for those pages. */
const WORKSPACE_STATIC_LEAF_ROUTES = [
  WORKSPACE_ROUTE_SETTINGS,
  WORKSPACE_ROUTE_EPISODES,
  WORKSPACE_ROUTE_CHARACTERS,
  WORKSPACE_ROUTE_SCENES,
  WORKSPACE_ROUTE_PROPS,
  WORKSPACE_ROUTE_PRODUCTS,
  WORKSPACE_ROUTE_FILES,
] as const;

/**
 * Actual registered subpaths beneath `/app/projects/:projectName`: `.../settings`
 * is the separately registered full-screen `ProjectSettingsPage` in router.tsx; the rest are
 * routes actually registered within the nested `StudioCanvasRouter` `<Switch>`.
 * Inner `<Switch>` fallbacks show empty canvas states; onboarding uses this to determine whether URLs fall within the main UI,
 * excluding unregistered subpaths.
 * wouter's underlying regexparam compiler uses the `i` flag (case-insensitive); mirror it
 * with `i` here, otherwise this pattern wrongly classifies valid case variants as unregistered subpaths.
 */
export const APP_PROJECT_WORKSPACE_PATTERN = new RegExp(
  `^${ROUTE_APP_PROJECTS}/[^/]+(/(?:${WORKSPACE_STATIC_LEAF_ROUTES.join("|")}|${WORKSPACE_ROUTE_EPISODES}/[^/]+))?$`,
  "i",
);
