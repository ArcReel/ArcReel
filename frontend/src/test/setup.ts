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

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.clearAllTimers();
  vi.useRealTimers();
  window.localStorage.clear();
  document.body.innerHTML = "";
});
