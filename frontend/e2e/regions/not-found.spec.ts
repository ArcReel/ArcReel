import type { Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";

// 404 页：未注册的地址，以及已移除、不再重定向的旧入口 lorebook 与 clues。
async function notFoundReady(page: Page) {
  await page.getByRole("heading", { name: "页面未找到" }).waitFor();
}

defineRegionScenarios("404 页", [
  {
    name: "打开不存在的地址",
    path: "/app/unknown",
    ready: notFoundReady,
    screenshot: { name: "not-found", target: (page) => page.getByRole("main") },
  },
  { name: "旧入口 lorebook 显示 404", path: "/app/projects/demo/lorebook", ready: notFoundReady },
  { name: "旧入口 clues 显示 404", path: "/app/projects/demo/clues", ready: notFoundReady },
]);
