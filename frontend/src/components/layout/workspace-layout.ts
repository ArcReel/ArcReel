import { useSyncExternalStore } from "react";
import { WORKSPACE_ROUTE_EPISODES } from "@/app-routes";

/** Sidebar icon-rail width when collapsed. */
export const SIDEBAR_RAIL_WIDTH = 56;
export const SIDEBAR_MIN_WIDTH = 200;
export const SIDEBAR_MAX_WIDTH = 320;
export const SIDEBAR_DEFAULT_WIDTH = 256;
/** Minimum canvas width when standard-mode Agent panels squeeze it; panel/sidebar drag limits narrow accordingly. */
export const CANVAS_MIN_WIDTH = 480;
/** Resize-handle distance per arrow-key press. */
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
    // Keep only in-memory values when localStorage is unavailable.
  }
}

export function readSidebarWidth(): number {
  const raw = readStorage(SIDEBAR_WIDTH_STORAGE_KEY);
  return raw ? clampSidebarWidth(parseInt(raw, 10)) : SIDEBAR_DEFAULT_WIDTH;
}

export function persistSidebarWidth(width: number): void {
  writeStorage(SIDEBAR_WIDTH_STORAGE_KEY, String(clampSidebarWidth(width)));
}

/** User-selected collapse state on pages without automatic collapse. */
export function readSidebarCollapsed(): boolean {
  return readStorage(SIDEBAR_COLLAPSED_STORAGE_KEY) === "true";
}

export function persistSidebarCollapsed(collapsed: boolean): void {
  writeStorage(SIDEBAR_COLLAPSED_STORAGE_KEY, String(collapsed));
}

/**
 * Reasons for sidebar icon-rail collapse: always in compact mode; in standard mode on episode pages (storyboard/editing) and episode-list views.
 * `location` is the workspace nested-route path (e.g. `/episodes/3`). Without reasons, follow user choices.
 */
export function sidebarAutoCollapseReason(location: string, compact: boolean): "compact" | "episodes" | null {
  if (compact) return "compact";
  const path = location.replace(/(.)\/$/, "$1");
  if (path === `/${WORKSPACE_ROUTE_EPISODES}` || path.startsWith(`/${WORKSPACE_ROUTE_EPISODES}/`)) return "episodes";
  return null;
}

// Only shells switch standard/compact modes by viewport; the breakpoint matches Tailwind xl.
const STANDARD_TIER_QUERY = "(min-width: 80rem)";

function subscribeTier(onChange: () => void): () => void {
  if (typeof window.matchMedia !== "function") return () => {};
  const query = window.matchMedia(STANDARD_TIER_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function isCompactTier(): boolean {
  // Test environments without matchMedia use standard mode.
  return typeof window.matchMedia === "function" && !window.matchMedia(STANDARD_TIER_QUERY).matches;
}

/** Viewports narrower than 1280 use compact mode: sidebar icon rails and Agent panels overlaying canvases. */
export function useCompactTier(): boolean {
  return useSyncExternalStore(subscribeTier, isCompactTier);
}

/** DOM IDs of header Agent toggles/panels: aria-controls connects toggles to panels; collapsed-panel focus returns to toggles. */
export const AGENT_PANEL_ID = "workspace-agent-panel";
export const AGENT_PANEL_TOGGLE_ID = "workspace-agent-toggle";
