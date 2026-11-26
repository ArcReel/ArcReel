import type { Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";

async function notFoundReady(page: Page) {
  await page.getByRole("heading", { name: "页面未找到" }).waitFor();
}

defineRegionScenarios("404 页", [
  {
    name: "打开不存在的地址",
    viewports: "both",
    a11y: true,
    path: "/app/unknown",
    ready: notFoundReady,
  },
]);
