import type { Page } from "@playwright/test";
import { waitForEntrance } from "./region-helpers.ts";
import { RECORDED_ACCESS_TOKEN } from "./recorded.ts";
import { expectAccessible, expectReachableLayout, test, type ApiOverrides } from "./test.ts";

export interface RegionScenario {
  name: string;
  path: string;
  auth?: "signed-in" | "signed-out";
  colorScheme?: "light" | "dark";
  api?: ApiOverrides;
  ready: (page: Page) => Promise<void>;
  act?: (page: Page) => Promise<void>;
  /**
   * 场景本身很重（如渲染 500 条列表再整页跑 axe）时设为 true，超时放宽为三倍：
   * 本机多 worker 并行时这类场景会超过默认的 30 秒。
   */
  slow?: boolean;
}

export function defineRegionScenarios(region: string, scenarios: RegionScenario[]) {
  test.describe(region, () => {
    for (const scenario of scenarios) {
      test(scenario.name, async ({ page, api }) => {
        test.slow(Boolean(scenario.slow));
        if (scenario.api) api.override(scenario.api);
        if (scenario.colorScheme) await page.emulateMedia({ colorScheme: scenario.colorScheme });
        if ((scenario.auth ?? "signed-in") === "signed-in") {
          await page.addInitScript((token) => localStorage.setItem("arcreel_auth_token", token), RECORDED_ACCESS_TOKEN);
        }

        await page.goto(scenario.path);
        await scenario.ready(page);
        await scenario.act?.(page);

        await waitForEntrance(page.locator("body"));
        await expectReachableLayout(page);
        await expectAccessible(page);
      });
    }
  });
}
