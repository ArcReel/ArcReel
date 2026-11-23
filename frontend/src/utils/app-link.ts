import {
  EPISODE_VIEW_EDIT,
  EPISODE_VIEW_PARAM,
  EPISODE_VIEW_TIMELINE_PARAM,
  ROUTE_APP,
  WORKSPACE_ROUTE_EPISODES,
} from "@/app-routes";

export const LINK_TIMELINE_PARAM = EPISODE_VIEW_TIMELINE_PARAM;
export const LINK_TIME_PARAM = "t";
export const LINK_UNIT_PARAM = "unit";

const EPISODE_PATH = new RegExp(`^${ROUTE_APP}/projects/[^/]+/${WORKSPACE_ROUTE_EPISODES}/[^/]+$`, "i");

export interface AppLink {
  href: string;
  to: string;
  unit: { id: string; seconds: number | null; project: string | null } | null;
}

export function parseSeconds(value: string | null): number | null {
  if (value === null || !/^\d+(\.\d+)?$/.test(value)) return null;
  const seconds = Number(value);
  return Number.isFinite(seconds) ? seconds : null;
}

function decodeProjectSegment(pathname: string): string | null {
  const segment = pathname.split("/")[3] ?? "";
  try {
    return decodeURIComponent(segment);
  } catch {
    return null;
  }
}

export function parseAppLink(href: string, origin: string): AppLink | null {
  let url: URL;
  try {
    url = new URL(href, origin);
  } catch {
    return null;
  }
  if (url.origin !== origin) return null;
  if (url.pathname !== ROUTE_APP && !url.pathname.startsWith(`${ROUTE_APP}/`)) return null;

  const normalizedHref = `${url.pathname}${url.search}${url.hash}`;
  const params = url.searchParams;
  let unit: AppLink["unit"] = null;
  const unitId = params.get(LINK_UNIT_PARAM);
  if (unitId && EPISODE_PATH.test(url.pathname) && params.get(EPISODE_VIEW_PARAM) !== EPISODE_VIEW_EDIT) {
    unit = { id: unitId, seconds: parseSeconds(params.get(LINK_TIME_PARAM)), project: decodeProjectSegment(url.pathname) };
    params.delete(LINK_UNIT_PARAM);
    params.delete(LINK_TIME_PARAM);
  }
  const query = params.toString();
  return { href: normalizedHref, to: `${url.pathname}${query ? `?${query}` : ""}${url.hash}`, unit };
}
