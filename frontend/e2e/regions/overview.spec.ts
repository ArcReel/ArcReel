import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Locator, Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { waitForEntrance } from "../support/region-helpers.ts";
import { RECORDED_DIR, type RecordedResponse } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// Overview: single constrained setting columns with header/asset completion/cost/story settings, plus empty-project welcomes.
const OVERVIEW_PATH = "/app/projects/demo";
const PROJECT = "GET /api/v1/projects/demo";
// Project events are unrecorded SSE; reject with nonretryable statuses, retaining recorded project data.
const EVENT_STREAM: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

interface RecordedProject {
  project: Record<string, unknown> & { episodes: Record<string, unknown>[]; status: Record<string, unknown> };
}

const recordedProject = (
  JSON.parse(readFileSync(join(RECORDED_DIR, "project-demo.json"), "utf8")) as RecordedResponse
).body as RecordedProject;
const recordedCost = (
  JSON.parse(readFileSync(join(RECORDED_DIR, "project-demo-cost-estimate.json"), "utf8")) as RecordedResponse
).body as Record<string, unknown>;
const [firstEpisode] = recordedProject.project.episodes;

function withProject(project: Record<string, unknown>): ApiOverrides {
  return { [PROJECT]: { status: 200, body: { ...recordedProject, project: { ...recordedProject.project, ...project } } } };
}

const repeat = (text: string, times: number) => Array.from({ length: times }, () => text).join("");

// Stress: long synopsis/world/genres/themes, forty episodes, stale assets of all kinds, multiple currencies/historical costs.
const LONG_PROJECT: ApiOverrides = {
  ...EVENT_STREAM,
  ...withProject({
    title: "长夜将尽：一座边城在三十年战乱里的兴衰与守城人的家族史",
    whole_source_files: [{ source_file: "source/长夜将尽.txt" }],
    overview: {
      synopsis: repeat(
        "边城被围的第三年，守城人的长子在城墙下捡到一封没有署名的降书。他没有交给父亲，而是带着它穿过敌营去找写信的人，一路上见到的村庄、河道与驿站，都在说明这场仗早就不是为了城本身。",
        6,
      ),
      genre: "历史战争 / 家族群像 / 悬疑与权谋交织的长篇叙事",
      theme: "忠诚与背叛的代价，以及一个家族怎样在三代人的选择里慢慢失去自己守护的东西",
      world_setting: repeat(
        "架空的北方王朝末年，边城依山而建，三面环水，城内实行军屯，粮草靠一条随季节改道的运河输送。",
        8,
      ),
    },
    episodes: Array.from({ length: 40 }, (_, i) => ({
      ...firstEpisode,
      episode: i + 1,
      title: `第 ${i + 1} 集`,
      script_file: `scripts/episode_${i + 1}.json`,
      duration_seconds: 95,
    })),
    status: {
      ...recordedProject.project.status,
      assets: {
        character: { total: 128, available: 120, stale: 14 },
        scene: { total: 96, available: 61, stale: 7 },
        prop: { total: 210, available: 210, stale: 23 },
        product: { total: 0, available: 0, stale: 0 },
      },
    },
  }),
  "GET /api/v1/projects/demo/cost-estimate": {
    status: 200,
    body: {
      ...recordedCost,
      project_totals: {
        estimate: { image: { USD: 412.5, CNY: 1280 }, video: { USD: 2310.75, CNY: 8400 }, audio: { CNY: 96 } },
        actual: {
          image: { USD: 120.4 },
          video: { USD: 860.2, CNY: 3120 },
          audio: { CNY: 45.5 },
          characters: { USD: 38.6 },
          scenes: { USD: 22.1 },
          props: { USD: 51.3 },
          unassigned: { USD: 17.25, CNY: 64 },
        },
      },
    },
  },
};

// Stress variant: incomplete cost figures; imported projects lack local generation records and six long-named custom-provider models lack prices.
const UNPRICED_MODELS = Array.from({ length: 6 }, (_, i) => ({
  call_type: i % 2 === 0 ? "image" : "video",
  provider: `custom-${i + 1}`,
  provider_name: `海外中转网关第 ${i + 1} 号线路（按量计费、需要手动维护价格表）`,
  model: `vendor-preview-${i % 2 === 0 ? "image" : "video"}-generation-model-2026-${String(i + 1).padStart(2, "0")}`,
  count: 12 + i,
}));
const UNPRICED_PROJECT: ApiOverrides = {
  ...LONG_PROJECT,
  "GET /api/v1/projects/demo/cost-estimate": {
    status: 200,
    body: {
      ...recordedCost,
      project_totals: { estimate: { video: { USD: 120.5 } }, actual: {} },
      unpriced: { estimate: UNPRICED_MODELS, actual: [] },
      missing_local_calls: true,
    },
  },
};

const EMPTY_PROJECT: ApiOverrides = {
  ...EVENT_STREAM,
  ...withProject({ overview: null, episodes: [], whole_source_files: [] }),
};

const SOURCE_ONLY_PROJECT: ApiOverrides = {
  ...EVENT_STREAM,
  ...withProject({ overview: null, whole_source_files: [{ source_file: "source/novel.txt" }] }),
};

const storySetting = (page: Page) => page.getByRole("region", { name: "故事设定" });

/** Compact Agent panels initially expand over canvas right sides; collapse to reveal entire content columns/right-side actions. */
async function collapseAgentPanel(page: Page) {
  const toggle = page.getByRole("button", { name: "Agent", exact: true });
  if ((await toggle.getAttribute("aria-pressed")) === "true") await toggle.click();
}

async function box(locator: Locator) {
  const rect = await locator.boundingBox();
  if (!rect) throw new Error("元素不可见");
  return rect;
}

async function overviewReady(page: Page) {
  await storySetting(page).waitFor();
  // Fonts/cost responses change content height; wait for actual pages to load before testing scroll reachability.
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
}

defineRegionScenarios("项目概览", [
  {
    name: "录制的演示项目：页头元信息、资产与费用两行、空的故事设定",
    path: OVERVIEW_PATH,
    api: EVENT_STREAM,
    ready: overviewReady,
    act: async (page) => {
      await expect(page.getByRole("heading", { level: 1, name: "演示项目" })).toBeVisible();
      await expect(storySetting(page).getByRole("textbox", { name: "梗概" })).toHaveValue("");
      // Width-limited mode: content columns cap at 760px, aligned with left canvas padding; extra wide-window whitespace stays right.
      await collapseAgentPanel(page);
      const canvas = await box(page.getByRole("main"));
      const column = await box(storySetting(page));
      const padding = canvas.width >= 768 ? 32 : 24;
      expect(column.x - canvas.x).toBe(padding);
      expect(column.width).toBeLessThanOrEqual(760);
      if ((page.viewportSize()?.width ?? 0) >= 2560) expect(column.width).toBe(760);
    },
    // Content columns: headers, asset/cost rows and story settings.
    screenshot: { name: "overview-column", target: (page) => storySetting(page).locator("xpath=..") },
  },
  {
    name: "长文本故事设定与多条资产过期：字段随内容撑高，由画布滚动到底",
    path: OVERVIEW_PATH,
    api: LONG_PROJECT,
    ready: overviewReady,
    act: async (page) => {
      await collapseAgentPanel(page);
      await expect(page.getByRole("link", { name: "道具 210/210 · 23 过期" })).toBeVisible();
      const world = storySetting(page).getByRole("textbox", { name: "世界观" });
      await world.scrollIntoViewIfNeeded();
      await expect(world).toBeInViewport();
    },
  },
  {
    name: "费用明细：多币种与历史支出在弹层里分两列，底部链接到使用记录",
    path: OVERVIEW_PATH,
    api: LONG_PROJECT,
    ready: overviewReady,
    act: async (page) => {
      await collapseAgentPanel(page);
      await page.getByRole("button", { name: "明细" }).click();
      const popover = page.getByRole("dialog", { name: "费用明细" });
      await popover.waitFor();
      await expect(popover.getByRole("link", { name: "查看使用记录" })).toBeInViewport();
    },
    screenshot: { name: "overview-cost-details", target: (page) => page.getByRole("dialog", { name: "费用明细" }) },
  },
  {
    name: "费用拿不到完整数字：说明原因，列出没有价格的模型并链接到设置价格",
    path: OVERVIEW_PATH,
    api: UNPRICED_PROJECT,
    ready: overviewReady,
    act: async (page) => {
      await collapseAgentPanel(page);
      await expect(page.getByText("已花 —")).toBeVisible();
      await expect(page.getByText(/本机没有这个项目的生成记录/)).toBeVisible();
      const lastModel = page.getByRole("link", { name: new RegExp(UNPRICED_MODELS[5].model) });
      await expect(lastModel).toHaveAttribute(
        "href",
        `/app/settings?section=providers&custom=6&model=${UNPRICED_MODELS[5].model}`,
      );
      await lastModel.scrollIntoViewIfNeeded();
      await expect(lastModel).toBeInViewport();
    },
    screenshot: {
      name: "overview-cost-unpriced",
      target: (page) => page.getByText("设置价格：").locator("xpath=../../.."),
    },
  },
  {
    name: "故事设定有修改时下方出现未保存提示条",
    path: OVERVIEW_PATH,
    api: LONG_PROJECT,
    ready: overviewReady,
    act: async (page) => {
      await collapseAgentPanel(page);
      await storySetting(page).getByRole("textbox", { name: "类型" }).fill("历史战争");
      const save = storySetting(page).getByRole("button", { name: "保存" });
      await waitForEntrance(page.locator("body"));
      await save.scrollIntoViewIfNeeded();
      await expect(save).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "有未保存修改时从原文重新生成：确认框说明修改会丢失",
    path: OVERVIEW_PATH,
    api: LONG_PROJECT,
    ready: overviewReady,
    act: async (page) => {
      await collapseAgentPanel(page);
      await storySetting(page).getByRole("textbox", { name: "主题" }).fill("忠诚");
      await storySetting(page).getByRole("button", { name: "放弃修改并重新生成" }).click();
      const dialog = page.getByRole("alertdialog", { name: "从原文重新生成故事设定？" });
      await dialog.waitFor();
      await expect(dialog.getByRole("button", { name: "放弃修改并重新生成" })).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "从原文生成时故事设定区显示骨架与「正在读取原文…」",
    path: OVERVIEW_PATH,
    api: SOURCE_ONLY_PROJECT,
    ready: overviewReady,
    act: async (page) => {
      // Hold generation requests without responses, retaining Reading states.
      await page.route("**/api/v1/projects/demo/generate-overview", () => {});
      await collapseAgentPanel(page);
      await storySetting(page).getByRole("button", { name: "从原文生成" }).click();
      await expect(storySetting(page).getByRole("status")).toHaveText("正在读取原文…");
    },
    screenshot: { name: "overview-reading-source", target: storySetting },
  },
  {
    name: "空项目欢迎页：标题、一句说明、拖放区与从空白开始的指引",
    path: OVERVIEW_PATH,
    api: EMPTY_PROJECT,
    ready: async (page) => {
      await page.getByRole("heading", { name: "开始制作《演示项目》" }).waitFor();
    },
    act: collapseAgentPanel,
    screenshot: {
      name: "overview-welcome",
      target: (page) => page.getByRole("heading", { name: "开始制作《演示项目》" }).locator("xpath=../.."),
    },
  },
]);
