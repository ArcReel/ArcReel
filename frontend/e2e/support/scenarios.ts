import type { Page } from "@playwright/test";
import { waitForEntrance } from "./region-helpers.ts";
import { RECORDED_ACCESS_TOKEN } from "./recorded.ts";
import { expectAccessible, expectReachableLayout, test, type ApiOverrides, type ApiStub } from "./test.ts";
import {
  DESKTOP_VIEWPORT,
  projectName,
  RESPONSIVE_TAG,
  VIEWPORT_CONTRACT_TAG,
  type AcceptanceViewport,
} from "./viewports.ts";

export interface RegionScenario {
  name: string;
  path: string;
  auth?: "signed-in" | "signed-out";
  colorScheme?: "light" | "dark";
  api?: ApiOverrides;
  ready: (page: Page) => Promise<void>;
  act?: (page: Page) => Promise<void>;
  viewports?: "desktop" | "both";
  a11y?: boolean | "all-viewports";
  layout?: boolean;
  slow?: boolean;
}

export interface ViewportContractScenario extends Omit<RegionScenario, "viewports"> {
  viewport: AcceptanceViewport;
}

const DESKTOP_PROJECT = projectName(DESKTOP_VIEWPORT);

function shouldCheckA11y(a11y: RegionScenario["a11y"], isContract: boolean) {
  if (!a11y) return false;
  if (isContract || a11y === "all-viewports") return true;
  return test.info().project.name === DESKTOP_PROJECT;
}

async function runScenario(
  scenario: Omit<RegionScenario, "viewports">,
  page: Page,
  api: ApiStub,
  isContract: boolean,
) {
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
  if (scenario.layout ?? true) await expectReachableLayout(page);
  if (shouldCheckA11y(scenario.a11y, isContract)) await expectAccessible(page);
}

export function defineRegionScenarios(region: string, scenarios: RegionScenario[]) {
  test.describe(region, () => {
    for (const scenario of scenarios) {
      const tag = scenario.viewports === "both" ? [RESPONSIVE_TAG] : [];
      test(scenario.name, { tag }, async ({ page, api }) => {
        await runScenario(scenario, page, api, false);
      });
    }
  });
}

export function defineViewportContracts(region: string, scenarios: ViewportContractScenario[]) {
  const byViewport = new Map<string, { viewport: AcceptanceViewport; items: ViewportContractScenario[] }>();
  for (const scenario of scenarios) {
    const key = projectName(scenario.viewport);
    const group = byViewport.get(key) ?? { viewport: scenario.viewport, items: [] };
    group.items.push(scenario);
    byViewport.set(key, group);
  }

  test.describe(region, () => {
    for (const [size, group] of byViewport) {
      test.describe(size, () => {
        test.use({ viewport: { width: group.viewport.width, height: group.viewport.height } });
        for (const scenario of group.items) {
          test(scenario.name, { tag: [VIEWPORT_CONTRACT_TAG] }, async ({ page, api }) => {
            await runScenario(scenario, page, api, true);
          });
        }
      });
    }
  });
}
