import { useSyncExternalStore } from "react";

/**
 * 主题偏好：本机偏好，不经后端保存。深色由 <html> 上的 .dark 类决定。
 * index.html 的内联脚本在首屏渲染前按同一个存储键和同一套规则加上 .dark，避免先闪一下错误的主题；改这里时一起改。
 */
export const THEME_PREFERENCES = ["light", "dark", "system"] as const;
export type ThemePreference = (typeof THEME_PREFERENCES)[number];

const STORAGE_KEY = "arcreel_theme";
const SYSTEM_DARK_QUERY = "(prefers-color-scheme: dark)";
const listeners = new Set<() => void>();

export function isThemePreference(value: unknown): value is ThemePreference {
  return THEME_PREFERENCES.includes(value as ThemePreference);
}

export function getThemePreference(): ThemePreference {
  const stored = window.localStorage.getItem(STORAGE_KEY);
  return isThemePreference(stored) ? stored : "system";
}

function systemPrefersDark(): boolean {
  // 取不到系统偏好时按深色处理
  return typeof window.matchMedia !== "function" || window.matchMedia(SYSTEM_DARK_QUERY).matches;
}

function applyTheme() {
  const preference = getThemePreference();
  document.documentElement.classList.toggle("dark", preference === "dark" || (preference === "system" && systemPrefersDark()));
}

function applyAndNotify() {
  applyTheme();
  for (const listener of listeners) listener();
}

export function setThemePreference(preference: ThemePreference) {
  window.localStorage.setItem(STORAGE_KEY, preference);
  applyAndNotify();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useThemePreference(): ThemePreference {
  return useSyncExternalStore(subscribe, getThemePreference);
}

/** 启动时调用一次：应用当前偏好，在跟随系统时随系统明暗实时切换，并跟随其他标签页改的偏好。 */
export function initTheme() {
  applyTheme();
  window.addEventListener("storage", (event) => {
    if (event.key === STORAGE_KEY || event.key === null) applyAndNotify();
  });
  if (typeof window.matchMedia === "function") {
    window.matchMedia(SYSTEM_DARK_QUERY).addEventListener("change", applyTheme);
  }
}
