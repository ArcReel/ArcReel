import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import i18n from "@/i18n";
import { voidCall } from "@/utils/async";

voidCall(i18n.changeLanguage("zh"));

// jsdom 默认不实现 ResizeObserver；@floating-ui/react 的 autoUpdate 会调它来
// 跟踪 reference / floating 元素尺寸变化。用空 stub 即可，测试只断言可见性、
// 交互与结构，不验位置像素。
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
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
