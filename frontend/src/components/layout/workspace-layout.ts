import { useSyncExternalStore } from "react";
import { WORKSPACE_ROUTE_EPISODES } from "@/app-routes";

/** 侧栏折叠后的图标栏宽度。 */
export const SIDEBAR_RAIL_WIDTH = 56;
export const SIDEBAR_MIN_WIDTH = 200;
export const SIDEBAR_MAX_WIDTH = 320;
export const SIDEBAR_DEFAULT_WIDTH = 256;
/** 标准档 Agent 面板挤压画布时，画布至少保留的宽度；面板与侧栏的可拖上限随之收窄。 */
export const CANVAS_MIN_WIDTH = 480;
/** 调宽手柄每按一次方向键移动的距离。 */
export const RESIZE_KEYBOARD_STEP = 16;

const SIDEBAR_WIDTH_STORAGE_KEY = "arcreel_workspace_sidebar_width";
const SIDEBAR_COLLAPSED_STORAGE_KEY = "arcreel_workspace_sidebar_collapsed";

export function clampSidebarWidth(value: number): number {
  if (!Number.isFinite(value)) return SIDEBAR_DEFAULT_WIDTH;
  return Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, Math.round(value)));
}

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // localStorage 不可用时只保留内存值
  }
}

export function readSidebarWidth(): number {
  const raw = readStorage(SIDEBAR_WIDTH_STORAGE_KEY);
  return raw ? clampSidebarWidth(parseInt(raw, 10)) : SIDEBAR_DEFAULT_WIDTH;
}

export function persistSidebarWidth(width: number): void {
  writeStorage(SIDEBAR_WIDTH_STORAGE_KEY, String(clampSidebarWidth(width)));
}

/** 用户在没有自动折叠的页面上手动选择的折叠状态。 */
export function readSidebarCollapsed(): boolean {
  return readStorage(SIDEBAR_COLLAPSED_STORAGE_KEY) === "true";
}

export function persistSidebarCollapsed(collapsed: boolean): void {
  writeStorage(SIDEBAR_COLLAPSED_STORAGE_KEY, String(collapsed));
}

/**
 * 侧栏自动折叠为图标栏的原因：紧凑档一律折叠；标准档在单集页（分镜、剪辑）与分集视图折叠。
 * `location` 是工作区嵌套路由内的路径（如 `/episodes/3`）。没有原因时侧栏跟随用户的选择。
 */
export function sidebarAutoCollapseReason(location: string, compact: boolean): "compact" | "episodes" | null {
  if (compact) return "compact";
  const path = location.replace(/(.)\/$/, "$1");
  if (path === `/${WORKSPACE_ROUTE_EPISODES}` || path.startsWith(`/${WORKSPACE_ROUTE_EPISODES}/`)) return "episodes";
  return null;
}

// 外壳是唯一按视口切换标准档与紧凑档的地方，断点与 Tailwind 的 xl 一致。
const STANDARD_TIER_QUERY = "(min-width: 80rem)";

function subscribeTier(onChange: () => void): () => void {
  if (typeof window.matchMedia !== "function") return () => {};
  const query = window.matchMedia(STANDARD_TIER_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function isCompactTier(): boolean {
  // 测试环境没有 matchMedia 时按标准档处理
  return typeof window.matchMedia === "function" && !window.matchMedia(STANDARD_TIER_QUERY).matches;
}

/** 视口宽度低于 1280 时为紧凑档：侧栏收为图标栏，Agent 面板改为覆盖画布。 */
export function useCompactTier(): boolean {
  return useSyncExternalStore(subscribeTier, isCompactTier);
}

/** 顶栏「Agent」开关与 Agent 面板的 DOM id：开关经 aria-controls 指向面板，面板收起时焦点回到开关。 */
export const AGENT_PANEL_ID = "workspace-agent-panel";
export const AGENT_PANEL_TOGGLE_ID = "workspace-agent-toggle";
