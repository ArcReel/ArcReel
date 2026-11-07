import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// i18n uses lazy backends; await resource loading before tests or initial t() returns keys rather than
// Chinese, failing assertions. LanguageDetector reads localStorage at init: prewrite zh to load only Chinese
// namespaces; otherwise jsdom navigator.language loads en-US/en before switching back to zh.
// This setup runs per test file; loading those two extra resource sets is pure overhead.
window.localStorage.setItem("i18nextLng", "zh");
const { i18nReady } = await import("@/i18n");
await i18nReady;

// jsdom lacks ResizeObserver; Base UI popover positioning (floating-ui autoUpdate),
// tab indicators and TruncatedText clipping detection use it to track sizes. Empty stubs suffice:
// tests assert visibility/interactions/structure, never pixel positioning.
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    constructor(_cb: ResizeObserverCallback) {}
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

// jsdom lacks scrollIntoView; cmdk (Command) uses it to reveal changed selections.
if (typeof Element !== "undefined" && typeof Element.prototype.scrollIntoView !== "function") {
  Element.prototype.scrollIntoView = () => {};
}

if (
  typeof window !== "undefined"
  && (
    typeof window.localStorage?.getItem !== "function"
    || typeof window.localStorage?.setItem !== "function"
    || typeof window.localStorage?.clear !== "function"
  )
) {
  const storage = new Map<string, string>();
  const localStorageMock: Storage = {
    get length() {
      return storage.size;
    },
    clear() {
      storage.clear();
    },
    getItem(key: string) {
      return storage.has(key) ? storage.get(key)! : null;
    },
    key(index: number) {
      return Array.from(storage.keys())[index] ?? null;
    },
    removeItem(key: string) {
      storage.delete(key);
    },
    setItem(key: string, value: string) {
      storage.set(String(key), String(value));
    },
  };
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: localStorageMock,
  });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.clearAllTimers();
  vi.useRealTimers();
  window.localStorage.clear();
  document.body.innerHTML = "";
});
