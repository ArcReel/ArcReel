/**
 * 应用内链接（Agent 回复里的「跳到这里看」）的格式与解析。链接是普通的应用内路径，新开标签页、复制地址都照常可用：
 *
 * - 剪辑视图：`/app/projects/{项目名}/episodes/{集 ID}?view=edit&tl={剪辑时间线ID}&t={秒}`
 *   `tl` 与 `t` 均可省略；`t` 是该剪辑时间线上的全局时间（秒），落在哪个片段由时间线决定。
 * - 视频单元预览：`/app/projects/{项目名}/episodes/{集 ID}?unit={单元ID}&t={秒}`
 *   打开该单元的预览；带 `t` 时从 `t` 开始播放。`t` 可省略，是该单元视频自身的时间（秒，从视频开头算起）；单元 ID 即脚本里的 `segment_id` / `shot_id` / `scene_id` / `unit_id`。
 *
 * 集 ID 是项目详情 `episodes[].episode`，不是播出位置。项目名按路径段做 URL 编码。两种格式不要混写：带 `view=edit` 时 `unit` 不生效，`t` 按剪辑时间线上的全局时间处理。
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
  /** 规范化后的完整站内地址（以 `/` 开头，含原有查询参数），用作 `<a href>`，新开标签页也落到同一处。 */
  href: string;
  /** 要跳转的路径与查询。单元链接的 `unit`、`t` 在这里已去掉：它们是一次性的定位指令，不留在地址栏里。 */
  to: string;
  /** 链接指向的视频单元；起始时间缺省为 null。 */
  unit: { id: string; seconds: number | null } | null;
}

/** 非负的十进制秒数；其余写法（负数、科学计数法、空串）一律视为没有写。 */
export function parseSeconds(value: string | null): number | null {
  if (value === null || !/^\d+(\.\d+)?$/.test(value)) return null;
  const seconds = Number(value);
  return Number.isFinite(seconds) ? seconds : null;
}

/** 同源且在 `/app` 之下的链接才算应用内链接；其余（含协议相对地址、其他域名）返回 null，由调用方按外链处理。 */
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
    unit = { id: unitId, seconds: parseSeconds(params.get(LINK_TIME_PARAM)) };
    params.delete(LINK_UNIT_PARAM);
    params.delete(LINK_TIME_PARAM);
  }
  const query = params.toString();
  return { href: normalizedHref, to: `${url.pathname}${query ? `?${query}` : ""}${url.hash}`, unit };
}
