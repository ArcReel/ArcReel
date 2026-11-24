import type { Locator, Page } from "@playwright/test";
import { isDefaultOrigin } from "./origin.ts";
import { waitForEntrance } from "./region-helpers.ts";
import { RECORDED_ACCESS_TOKEN } from "./recorded.ts";
import { VIEWPORTS, projectName } from "./viewports.ts";
import { expect, expectAccessible, expectReachableLayout, test, type ApiOverrides } from "./test.ts";

const SCREENSHOT_PROJECTS = new Set(VIEWPORTS.filter((viewport) => viewport.screenshot).map(projectName));
const LIGHT_SCREENSHOT_PROJECTS = new Set(["1440x900"]);

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
  /**
   * 区域截图，是回归闸门：CI 的 frontend-e2e 设置 E2E_SCREENSHOTS=1，比对出差异或缺少基线即失败。
   * 基线在官方 Playwright 镜像里渲染（pnpm e2e:remote），宿主机的字体渲染与之不同，
   * 所以只在连容器运行时设置该变量。优先截区域而非整页。
   */
  screenshot?: {
    name: string;
    target?: (page: Page) => Locator;
    /** 截图里有 `window.location.origin` 拼出的地址：基线按默认端口生成，换端口运行时这张不比对。 */
    showsOrigin?: boolean;
  };
}

export function defineRegionScenarios(region: string, scenarios: RegionScenario[]) {
  test.describe(region, () => {
    for (const scenario of scenarios) {
      test(scenario.name, async ({ page, api }, testInfo) => {
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

        const { screenshot } = scenario;
        const projects = scenario.colorScheme === "light" ? LIGHT_SCREENSHOT_PROJECTS : SCREENSHOT_PROJECTS;
        if (screenshot && process.env.E2E_SCREENSHOTS && projects.has(testInfo.project.name)) {
          if (screenshot.showsOrigin && !isDefaultOrigin(page.url())) {
            testInfo.annotations.push({ type: "screenshot", description: `${screenshot.name}：非默认端口，不比对` });
            return;
          }
          await expect(screenshot.target?.(page) ?? page).toHaveScreenshot(`${screenshot.name}.png`);
        }
      });
    }
  });
}
