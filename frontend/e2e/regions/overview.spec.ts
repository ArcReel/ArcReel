import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Locator, Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { RECORDED_DIR, type RecordedResponse } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 项目概览：单列限宽的设定页（页头、资产完成度与费用两行、故事设定），以及空项目的欢迎页。
const OVERVIEW_PATH = "/app/projects/demo";
const PROJECT = "GET /api/v1/projects/demo";
// 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在录制的项目数据上。
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

// 压力变体：长梗概与长世界观、很长的类型与主题、四十集、各类资产图都有过期，费用含多币种与历史支出。
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

const EMPTY_PROJECT: ApiOverrides = {
  ...EVENT_STREAM,
  ...withProject({ overview: null, episodes: [], whole_source_files: [] }),
};

const SOURCE_ONLY_PROJECT: ApiOverrides = {
  ...EVENT_STREAM,
  ...withProject({ overview: null, whole_source_files: [{ source_file: "source/novel.txt" }] }),
};

const storySetting = (page: Page) => page.getByRole("region", { name: "故事设定" });

/** 等弹层进出场与配色过渡结束：axe 会把过渡中间色判为对比度不足。过渡被取消时 finished 以 AbortError 拒绝。 */
async function settle(page: Page) {
  await page.evaluate(() => Promise.allSettled(document.getAnimations().map((animation) => animation.finished)));
}

/** 紧凑档的 Agent 面板默认展开并盖住画布右侧，先收起，内容列与画布右侧的操作才完整可见。 */
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
      // 限宽档：内容列最宽 760px，紧贴画布左侧的内边距，宽窗口多出的空白留在右侧
      await collapseAgentPanel(page);
      const canvas = await box(page.getByRole("main"));
      const column = await box(storySetting(page));
      const padding = canvas.width >= 768 ? 32 : 24;
      expect(column.x - canvas.x).toBe(padding);
      expect(column.width).toBeLessThanOrEqual(760);
      if ((page.viewportSize()?.width ?? 0) >= 2560) expect(column.width).toBe(760);
    },
    // 内容列：页头、资产与费用两行、故事设定
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
      await settle(page);
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
      await settle(page);
      await expect(popover.getByRole("link", { name: "查看使用记录" })).toBeInViewport();
    },
    screenshot: { name: "overview-cost-details", target: (page) => page.getByRole("dialog", { name: "费用明细" }) },
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
      await save.scrollIntoViewIfNeeded();
      await expect(save).toBeInViewport({ ratio: 1 });
      await settle(page);
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
      await settle(page);
      await expect(dialog.getByRole("button", { name: "放弃修改并重新生成" })).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "从原文生成时故事设定区显示骨架与「正在读取原文…」",
    path: OVERVIEW_PATH,
    api: SOURCE_ONLY_PROJECT,
    ready: overviewReady,
    act: async (page) => {
      // 生成请求挂起不返回，停在读取中的状态
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
