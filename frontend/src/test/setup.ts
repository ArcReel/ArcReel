import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// i18n 走 lazy backend，测试运行前必须 await 资源加载完，否则首次 t() 返回 key 字符串而不是
// 中文，断言会失败。LanguageDetector 在 init 时读 localStorage：先写入 zh，它就只加载中文
// 这一套 namespace；不写的话会按 jsdom 的 navigator.language 先加载 en-US 与 en，再切回 zh。
// 这段 setup 每个测试文件都要跑一遍，多加载的两套资源是纯开销。
window.localStorage.setItem("i18nextLng", "zh");
const { i18nReady } = await import("@/i18n");
await i18nReady;

// jsdom 默认不实现 ResizeObserver；Base UI 弹层定位（经 floating-ui 的 autoUpdate）、
// Tabs 指示条与 TruncatedText 的截断检测都会调它跟踪尺寸变化。用空 stub 即可，
// 测试只断言可见性、交互与结构，不验位置像素。
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    constructor(_cb: ResizeObserverCallback) {}
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

// jsdom 不实现 scrollIntoView；cmdk（Command）在选中项变化时调用它把选中项滚进视野。
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
