// Regional scenario registration: each e2e/regions/ area registers through defineRegionScenarios,
// running overflow/axe on all acceptance viewports. Focus on available-height-changing states:
// open pages, expand height-changing panels and open overlays before probing.
import type { Locator, Page } from "@playwright/test";
import { isDefaultOrigin } from "./origin.ts";
import { waitForEntrance } from "./region-helpers.ts";
import { RECORDED_ACCESS_TOKEN } from "./recorded.ts";
import { expect, expectAccessible, expectReachableLayout, test, type ApiOverrides } from "./test.ts";

// Screenshot viewports are maintained separately from overflow lists. Light mode samples one viewport only.
const SCREENSHOT_PROJECTS = new Set(["1024x600", "1440x900", "2560x1440"]);
const LIGHT_SCREENSHOT_PROJECTS = new Set(["1440x900"]);

export interface RegionScenario {
  name: string;
  /** Opened routes. */
  path: string;
  /** Authenticated by default. */
  auth?: "signed-in" | "signed-out";
  /** System theme defaults dark; preferences retain Follow system defaults. */
  colorScheme?: "light" | "dark";
  /** Override recorded APIs for long-text/many-item stress variants. */
  api?: ApiOverrides;
  /** Await regional rendering, for example visible landmark elements. */
  ready: (page: Page) => Promise<void>;
  /** Reach probed states, for example expanded panels/open overlays. */
  act?: (page: Page) => Promise<void>;
  /**
   * Heavy scenarios (for example 500-item rendering plus whole-page axe) set true for triple timeouts:
   * local parallel workers can exceed default 30 seconds.
   */
  slow?: boolean;
  /**
   * Region screenshots are regression gates: CI frontend-e2e sets E2E_SCREENSHOTS=1, failing differences/missing baselines.
   * Baselines render in official Playwright images (pnpm e2e:remote), differing from host fonts,
   * so set this variable for container connections only. Prefer regions over whole pages.
   */
  screenshot?: {
    name: string;
    target?: (page: Page) => Locator;
    /** Screenshots containing `window.location.origin` addresses use default-port baselines; skip comparisons on alternate ports. */
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

        // Layout/axe inspect final visible states, including finite overlay-mask/background entrance animations.
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
          // toHaveScreenshot already waits for document.fonts.ready and two consecutive identical frames.
          await expect(screenshot.target?.(page) ?? page).toHaveScreenshot(`${screenshot.name}.png`);
        }
      });
    }
  });
}
