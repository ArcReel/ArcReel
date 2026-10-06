// 区域场景注册：每个页面区域在 e2e/regions/ 下用 defineRegionScenarios 登记自己的场景，
// 每个场景在全部验收视口上跑溢出探针与 axe。场景以会改变可用高度的状态为主：
// 打开页面、展开会撑高的面板、打开弹层（弹层先打开再探测）。
import type { Locator, Page } from "@playwright/test";
import { isDefaultOrigin } from "./origin.ts";
import { waitForEntrance } from "./region-helpers.ts";
import { RECORDED_ACCESS_TOKEN } from "./recorded.ts";
import { expect, expectAccessible, expectReachableLayout, test, type ApiOverrides } from "./test.ts";

// 截图只在这些视口拍，和溢出探针的视口清单分开维护。
const SCREENSHOT_PROJECTS = new Set(["1024x600", "1440x900", "2560x1440"]);

export interface RegionScenario {
  name: string;
  /** 打开的路由。 */
  path: string;
  /** 默认已登录。 */
  auth?: "signed-in" | "signed-out";
  /** 替换录制的接口响应，用于长文本、多条目等压力变体。 */
  api?: ApiOverrides;
  /** 等到区域渲染完成，例如等待标志性元素可见。 */
  ready: (page: Page) => Promise<void>;
  /** 把页面带到要探测的状态，例如展开面板、打开弹层。 */
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
        if ((scenario.auth ?? "signed-in") === "signed-in") {
          await page.addInitScript((token) => localStorage.setItem("arcreel_auth_token", token), RECORDED_ACCESS_TOKEN);
        }

        await page.goto(scenario.path);
        await scenario.ready(page);
        await scenario.act?.(page);

        // 布局与 axe 都检查最终可见状态，包含弹层遮罩与背景中的有限入场动画。
        await waitForEntrance(page.locator("body"));
        await expectReachableLayout(page);
        await expectAccessible(page);

        const { screenshot } = scenario;
        if (screenshot && process.env.E2E_SCREENSHOTS && SCREENSHOT_PROJECTS.has(testInfo.project.name)) {
          if (screenshot.showsOrigin && !isDefaultOrigin(page.url())) {
            testInfo.annotations.push({ type: "screenshot", description: `${screenshot.name}：非默认端口，不比对` });
            return;
          }
          // toHaveScreenshot 自带等待 document.fonts.ready 与连续两帧一致。
          await expect(screenshot.target?.(page) ?? page).toHaveScreenshot(`${screenshot.name}.png`);
        }
      });
    }
  });
}
