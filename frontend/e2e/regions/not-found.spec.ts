import type { Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";

// 404 for unregistered app URLs. Unknown project subpaths show canvas empty states; see workspace-header.spec.ts.
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
]);
