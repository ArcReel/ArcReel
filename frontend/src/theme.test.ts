import { afterEach, expect, it, vi } from "vitest";

import { initTheme } from "./theme";

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.classList.remove("dark");
});

it("跟随系统时，系统切换明暗后页面实时跟着变", () => {
  let systemDark = false;
  let onSystemChange = () => {};
  vi.stubGlobal("matchMedia", (query: string) => ({
    get matches() {
      return systemDark;
    },
    media: query,
    addEventListener: (_: string, listener: () => void) => {
      onSystemChange = listener;
    },
    removeEventListener: () => {},
  }));

  initTheme();
  expect(document.documentElement).not.toHaveClass("dark");

  systemDark = true;
  onSystemChange();
  expect(document.documentElement).toHaveClass("dark");
});

it("其他标签页改了主题，当前页跟着变", () => {
  window.localStorage.setItem("arcreel_theme", "light");
  initTheme();
  expect(document.documentElement).not.toHaveClass("dark");

  window.localStorage.setItem("arcreel_theme", "dark");
  window.dispatchEvent(new StorageEvent("storage", { key: "arcreel_theme", newValue: "dark" }));
  expect(document.documentElement).toHaveClass("dark");
});
