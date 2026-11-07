import { useSyncExternalStore } from "react";

/**
 * Theme preference is local to this machine, without backend persistence. The .dark class on <html> determines dark mode.
 * The inline index.html script applies .dark using the same storage key/rules before first render to prevent incorrect-theme flashes; update both together.
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
  // Use dark mode when system preference is unavailable
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

/** Call once at startup: apply the current preference, follow live system light/dark changes in system mode, and follow preference changes from other tabs. */
export function initTheme() {
  applyTheme();
  window.addEventListener("storage", (event) => {
    if (event.key === STORAGE_KEY || event.key === null) applyAndNotify();
  });
  if (typeof window.matchMedia === "function") {
    window.matchMedia(SYSTEM_DARK_QUERY).addEventListener("change", applyTheme);
  }
}
