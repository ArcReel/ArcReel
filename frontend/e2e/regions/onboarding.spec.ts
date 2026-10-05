import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";
import { clearAgentOverlay } from "../support/region-helpers.ts";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { RECORDED_DIR, type RecordedResponse } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 首次使用引导：driver.js 气泡、大厅「示例项目」区块、只读演示工作台（Agent 面板、顶栏徽标），
// 以及真实项目故事设定区底部的一次性就地提示。引导只在服务端报告未看过时自动启动。

const LOBBY_PATH = "/app/projects";
const DEMO_PATH = "/app/projects/onboarding_demo";

const UNSEEN: ApiOverrides = { "GET /api/v1/onboarding/status": { status: 200, body: { seen: false } } };
const EMPTY_LOBBY: ApiOverrides = { ...UNSEEN, "GET /api/v1/projects": { status: 200, body: { projects: [] } } };

// 气泡的标题与按钮按 driver.js 的类名取，越南语场景也能复用。
const tour = (page: Page) => page.locator(".driver-popover");
const tourTitle = (page: Page) => tour(page).locator(".driver-popover-title");

async function tourReady(page: Page) {
  await expect(tourTitle(page)).toBeVisible();
}

/** 点「下一步」，等气泡换成下一步的标题。换页的步骤要等新页面上的锚点挂载，气泡才会重绘。 */
async function next(page: Page) {
  const previous = await tourTitle(page).textContent();
  await tour(page).locator(".driver-popover-next-btn").click();
  await expect(tourTitle(page)).not.toHaveText(previous ?? "");
  await expect(tourTitle(page)).toBeVisible();
}

/** 遮罩上凿开的高亮框对准当前锚点：driver 把高亮框写成 SVG 路径的第二段，上沿是锚点上沿减去 stagePadding。 */
async function expectHighlightOnAnchor(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(() => {
        const d = document.querySelector(".driver-overlay path")?.getAttribute("d") ?? "";
        const stageTop = Number(/M[\d.]+,([\d.]+) h/.exec(d)?.[1]);
        const anchorTop = document.querySelector(".driver-active-element")?.getBoundingClientRect().top ?? NaN;
        return Math.abs(stageTop - (anchorTop - 8));
      }),
    )
    .toBeLessThan(1);
}

async function advance(page: Page, steps: number) {
  for (let i = 0; i < steps; i++) await next(page);
}

// 就地提示：项目先没有故事设定（有原文），从原文生成后变为有内容。
const PROJECT = "GET /api/v1/projects/demo";
const recordedProject = (
  JSON.parse(readFileSync(join(RECORDED_DIR, "project-demo.json"), "utf8")) as RecordedResponse
).body as { project: Record<string, unknown> };
const OVERVIEW = {
  synopsis: "灯塔守夜人在雾季收到一封三十年前寄出的信，循着邮戳找回失踪的邮差。",
  genre: "悬疑",
  theme: "迟到的消息与没说出口的话",
  world_setting: "常年起雾的港口小城，灯塔、邮局与渡轮码头。",
};
const SOURCE_ONLY: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
  [PROJECT]: {
    status: 200,
    body: {
      ...recordedProject,
      project: { ...recordedProject.project, overview: undefined, whole_source_files: [{ source_file: "source/novel.txt" }] },
    },
  },
};

defineRegionScenarios("新手引导", [
  {
    name: "首次进入大厅：欢迎气泡居中，遮罩盖住页面",
    path: LOBBY_PATH,
    api: UNSEEN,
    ready: tourReady,
    act: async (page) => {
      await expect(tourTitle(page)).toHaveText("欢迎使用 ArcReel");
      await expect(tour(page)).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "onboarding-welcome" },
  },
  {
    name: "第 6 步：空大厅上方出现「示例项目」区块，空状态保持不变",
    path: LOBBY_PATH,
    api: EMPTY_LOBBY,
    ready: tourReady,
    act: async (page) => {
      await advance(page, 5);
      await expect(tourTitle(page)).toHaveText("演示项目");
      await expect(page.getByRole("region", { name: "示例项目" })).toBeVisible();
      await expect(page.getByText("还没有项目")).toBeVisible();
      // 从设置页回到大厅时问候区晚于这个区块渲染，高亮框要跟着区块下移
      await expectHighlightOnAnchor(page);
      await expect(tour(page)).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "onboarding-demo-section" },
  },
  {
    name: "第 7 步：演示 Agent 面板，工序行与禁用的输入框",
    path: LOBBY_PATH,
    api: UNSEEN,
    ready: tourReady,
    act: async (page) => {
      await advance(page, 6);
      await expect(tourTitle(page)).toHaveText("Agent");
      await expect(page).toHaveURL(DEMO_PATH);
      await expect(page.getByText("开始制作", { exact: true })).toBeVisible();
      await expect(page.getByRole("textbox", { name: /./ }).last()).toBeDisabled();
      await expect(tour(page)).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "onboarding-demo-agent" },
  },
  {
    name: "第 10 步：落到集页的分镜视图",
    path: LOBBY_PATH,
    api: UNSEEN,
    ready: tourReady,
    act: async (page) => {
      await advance(page, 9);
      await expect(tourTitle(page)).toHaveText("分镜");
      await expect(page).toHaveURL(/\/episodes\/1\?view=board$/);
      await expect(tour(page)).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "越南语文案最长：每一步的气泡都完整落在视口内",
    path: LOBBY_PATH,
    api: UNSEEN,
    ready: async (page) => {
      await page.evaluate(() => localStorage.setItem("i18nextLng", "vi"));
      await page.reload();
      await tourReady(page);
    },
    act: async (page) => {
      await expect(tour(page)).toBeInViewport({ ratio: 1 });
      for (let step = 2; step <= 12; step++) {
        await next(page);
        await expect(tour(page)).toBeInViewport({ ratio: 1 });
      }
    },
  },
  {
    name: "演示工作台：顶栏「演示 · 只读」徽标聚焦后显示说明",
    path: DEMO_PATH,
    ready: async (page) => {
      await page.getByText("演示 · 只读").waitFor();
    },
    act: async (page) => {
      // 说明只在键盘聚焦或悬停时出现：从项目切换器 Tab 过来
      await page.getByRole("button", { name: /^切换项目/ }).focus();
      await page.keyboard.press("Tab");
      await expect(page.getByText("演示 · 只读")).toBeFocused();
      await expect(page.locator("[data-slot=tooltip-content]")).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "onboarding-demo-badge", target: (page) => page.getByRole("banner") },
  },
  {
    name: "故事设定从原文生成后，底部出现一次性提示",
    path: "/app/projects/demo",
    api: SOURCE_ONLY,
    ready: async (page) => {
      await page.getByRole("button", { name: "从原文生成" }).waitFor();
    },
    act: async (page) => {
      // 生成成功后重新拉取项目，这时故事设定已有内容
      await page.route("**/api/v1/projects/demo/generate-overview", (route) =>
        route.fulfill({ json: { success: true, overview: OVERVIEW } }),
      );
      await page.route("**/api/v1/projects/demo", (route) => route.fulfill({ json: { ...recordedProject, project: { ...recordedProject.project, overview: OVERVIEW } } }));
      await clearAgentOverlay(page);
      await page.getByRole("button", { name: "从原文生成" }).click();
      const tip = page.getByRole("status").filter({ hasText: "故事设定已提炼完成" });
      await tip.scrollIntoViewIfNeeded();
      await expect(tip).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "overview-handoff-tip", target: (page) => page.getByRole("status").filter({ hasText: "故事设定已提炼完成" }) },
  },
]);
