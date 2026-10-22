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
