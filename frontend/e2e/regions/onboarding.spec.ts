import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";
import { clearAgentOverlay } from "../support/region-helpers.ts";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { RECORDED_ACCESS_TOKEN, RECORDED_DIR, type RecordedResponse } from "../support/recorded.ts";
import { expect, test, type ApiOverrides } from "../support/test.ts";

// First-use onboarding: driver.js popovers, lobby Example projects, readonly demo workbenches with Agent/header badges,
// and one-time inline real-project story-setting tips. Autostart only when servers report unseen.

const LOBBY_PATH = "/app/projects";
const DEMO_PATH = "/app/projects/onboarding_demo";

const UNSEEN: ApiOverrides = { "GET /api/v1/onboarding/status": { status: 200, body: { seen: false } } };
const EMPTY_LOBBY: ApiOverrides = { ...UNSEEN, "GET /api/v1/projects": { status: 200, body: { projects: [] } } };

// Popover titles/actions use driver.js classes, reusable across languages.
const tour = (page: Page) => page.locator(".driver-popover");
const tourTitle = (page: Page) => tour(page).locator(".driver-popover-title");

async function tourReady(page: Page) {
  await expect(tourTitle(page)).toBeVisible();
}

/** Click Next and await changed step titles; cross-page steps wait for new anchors before popover repaint. */
async function next(page: Page) {
  const previous = await tourTitle(page).textContent();
  await tour(page).locator(".driver-popover-next-btn").click();
  await expect(tourTitle(page)).not.toHaveText(previous ?? "");
  await expect(tourTitle(page)).toBeVisible();
}

/** Mask highlights align with anchors: drivers write SVG second paths, with tops at anchor tops minus stagePadding. */
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

/** Popovers sit beside highlighted anchors without covering them; rectangles never overlap. */
async function expectPopoverBesideAnchor(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(() => {
        const popover = document.querySelector(".driver-popover")?.getBoundingClientRect();
        const anchor = document.querySelector(".driver-active-element")?.getBoundingClientRect();
        if (!popover || !anchor) return NaN;
        const width = Math.min(popover.right, anchor.right) - Math.max(popover.left, anchor.left);
        const height = Math.min(popover.bottom, anchor.bottom) - Math.max(popover.top, anchor.top);
        return Math.max(0, width) * Math.max(0, height);
      }),
    )
    .toBe(0);
}

async function advance(page: Page, steps: number) {
  for (let i = 0; i < steps; i++) await next(page);
}

// Inline tips: projects start without story settings but with sources, then generate populated settings.
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
      // Returning from settings renders greetings after this section, requiring highlights/popovers to follow downward movement.
      await expectHighlightOnAnchor(page);
      await expectPopoverBesideAnchor(page);
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
  ...([
    ["vi", "越南语", "Chào mừng đến với ArcReel"],
    ["ru", "俄语", "Добро пожаловать в ArcReel"],
  ] as const).map(([locale, label, welcomeTitle]) => ({
    name: `${label}：每一步的气泡都完整落在视口内`,
    path: LOBBY_PATH,
    api: UNSEEN,
    ready: async (page: Page) => {
      await page.evaluate((language) => localStorage.setItem("i18nextLng", language), locale);
      await page.reload();
      await tourReady(page);
    },
    act: async (page: Page) => {
      await expect(tourTitle(page)).toHaveText(welcomeTitle);
      await expect(tour(page)).toBeInViewport({ ratio: 1 });
      for (let step = 2; step <= 12; step++) {
        await next(page);
        await expect(tour(page)).toBeInViewport({ ratio: 1 });
      }
    },
  })),
  {
    name: "演示工作台：顶栏「演示 · 只读」徽标聚焦后显示说明",
    path: DEMO_PATH,
    ready: async (page) => {
      await page.getByText("演示 · 只读").waitFor();
    },
    act: async (page) => {
      // Explanations appear only on keyboard focus/hover: Tab here from project switchers.
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
      // Refetch projects after successful generation; story settings now contain content.
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


test("已有新建项目向导时启动引导：焦点只归气泡，结束后归还对话框", async ({ page, api }) => {
  api.override({ "POST /api/v1/onboarding/seen": { status: 200, body: { success: true } } });
  let releaseStatus!: () => void;
  const released = new Promise<void>((resolve) => { releaseStatus = resolve; });
  await page.route("**/api/v1/onboarding/status", async (route) => {
    await released;
    await route.fulfill({ json: { seen: false } });
  });
  await page.addInitScript((token) => localStorage.setItem("arcreel_auth_token", token), RECORDED_ACCESS_TOKEN);
  await page.goto(LOBBY_PATH);
  await page.getByRole("button", { name: "新建项目" }).first().click();
  const wizard = page.locator("[data-slot=dialog-content]").filter({ has: page.getByRole("heading", { name: "新建项目" }) });
  const name = wizard.getByRole("textbox").first();
  await name.focus();
  releaseStatus();
  await tourReady(page);

  for (let i = 0; i < 4; i++) {
    await page.keyboard.press("Tab");
    await expect.poll(() => page.evaluate(() =>
      Boolean(document.activeElement?.closest(".driver-popover")),
    )).toBe(true);
  }
  await tour(page).locator(".driver-popover-next-btn").focus();
  await page.keyboard.press("Enter");
  await expect(tourTitle(page)).not.toHaveText("欢迎使用 ArcReel");
  await expect(wizard).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(tour(page)).toHaveCount(0);
  await expect(name).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(wizard).toHaveCount(0);
});
