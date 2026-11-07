/**
 * In-app links for Agent reply navigation, ordinary application paths supporting new tabs/copied addresses:
 *
 * - Edit views: `/app/projects/{项目名}/episodes/{集 ID}?view=edit&tl={剪辑时间线ID}&t={秒}`
 *   `tl` / `t` are optional; `t` is global timeline seconds, with timelines choosing clips.
 * - Video-unit previews: `/app/projects/{项目名}/episodes/{集 ID}?unit={单元ID}&t={秒}`
 *   Open unit previews, with `t`, starting at `t`. Optional `t` is seconds from that unit video's beginning; IDs are script `segment_id` / `shot_id` / `scene_id` / `unit_id`.
 *
 * Episode IDs come from project-detail `episodes[].episode`, never broadcast positions. Encode project names as URL segments. Never mix formats: `view=edit` ignores `unit`, interpreting `t` as global timeline time.
 */
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
  /** Normalized complete application URLs (starting `/`, preserving queries), used for `<a href>` so new tabs reach the same locations. */
  href: string;
  /** Target paths/queries; unit-link `unit`/`t` are removed here as one-shot location commands never retained in address bars. */
  to: string;
  /** Linked video units; omitted start times are null. `project` is decoded project names, null when decoding fails. */
  unit: { id: string; seconds: number | null; project: string | null } | null;
}

/** Nonnegative decimal seconds; all other forms (negative, scientific notation, empty) count as omitted. */
export function parseSeconds(value: string | null): number | null {
  if (value === null || !/^\d+(\.\d+)?$/.test(value)) return null;
  const seconds = Number(value);
  return Number.isFinite(seconds) ? seconds : null;
}

/** Extract/decode project names from episode paths `/app/projects/{项目名}/...`; return null on decode failures. */
function decodeProjectSegment(pathname: string): string | null {
  const segment = pathname.split("/")[3] ?? "";
  try {
    return decodeURIComponent(segment);
  } catch {
    return null;
  }
}

/** Only same-origin links under `/app` are in-app; others (including protocol-relative/other domains) return null for caller external-link handling. */
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
