import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import i18n, { i18nReady } from "@/i18n";

// i18n 改为 lazy backend (issue #489) 后，测试运行前必须 await 资源加载完，
// 否则首次 t() 返回 key 字符串而不是中文，断言会失败。
await i18nReady;
await i18n.changeLanguage("zh");

// jsdom 默认不实现 ResizeObserver；@floating-ui/react 的 autoUpdate 会调它来
// 跟踪 reference / floating 元素尺寸变化。用空 stub 即可，测试只断言可见性、
// 交互与结构，不验位置像素。
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    constructor(_cb: ResizeObserverCallback) {}
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
