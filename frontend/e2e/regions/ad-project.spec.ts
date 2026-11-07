import type { Page } from "@playwright/test";
import { box, clearAgentOverlay, waitForEntrance } from "../support/region-helpers.ts";
import { recorded } from "../support/recorded.ts";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// Ad projects: persistent overview Creative brief/Products handle initial input; story settings live in video-page Story settings tabs.
// Convert recorded demos to ads, overriding all data within scenarios.
const OVERVIEW_PATH = "/app/projects/demo";
const SETTING_PATH = "/app/projects/demo/episodes/1?view=setting";
const PROJECT = "GET /api/v1/projects/demo";
// Project events are unrecorded SSE; reject nonretryably, retaining replacement project data.
const EVENT_STREAM: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

interface RecordedProject {
  project: Record<string, unknown> & { episodes: Record<string, unknown>[] };
}
const recordedProject = recorded<RecordedProject>("project-demo.json");
const [firstEpisode] = recordedProject.project.episodes;

const repeat = (text: string, times: number) => Array.from({ length: times }, () => text).join("");

function adProject(project: Record<string, unknown>): ApiOverrides {
  return {
    ...EVENT_STREAM,
    [PROJECT]: {
      status: 200,
      body: {
        ...recordedProject,
        project: {
          ...recordedProject.project,
          content_mode: "ad",
          target_duration: 30,
          brief: "",
          products: {},
          overview: null,
          episodes: [{ ...firstEpisode, episode: 1, title: "" }],
          ...project,
        },
      },
    },
  };
}

// Newly created ads lack briefs/products.
const NEW_AD = adProject({});

// Stress: long briefs/custom targets, twelve long-named/described products, and long minute/second-script synopses.
const LONG_AD = adProject({
  title: "夏日汽水：给通勤路上的年轻人一口能喝出海风的清爽，并在三十秒里讲清楚三种口味",
  target_duration: 45,
  brief: repeat(
    "面向一二线城市的通勤年轻人，调性轻快、带一点自嘲幽默；重点说清低糖、真果汁与气泡足三个卖点，结尾引导到小程序领券。",
    5,
  ),
  products: Object.fromEntries(
    Array.from({ length: 12 }, (_, i) => [
      `海盐柠檬气泡水·限定口味第 ${i + 1} 号（330ml 罐装，六罐家庭分享装）`,
      {
        description: repeat("透明玻璃感罐身，柠檬黄与海蓝渐变，罐口凝着水珠，开罐时有清晰的气泡声。", 3),
        selling_points: ["低糖", "真果汁"],
      },
    ]),
  ),
  overview: {
    synopsis: Array.from({ length: 12 }, (_, i) => `${i * 3}-${i * 3 + 3} 秒：冰块落进杯子，镜头推近，气泡沿杯壁上升。`).join("\n"),
    genre: "饮品广告 / 生活方式 / 轻喜剧",
    theme: "忙碌通勤里的一口清凉",
    world_setting: repeat("夏天傍晚的城市街角，便利店灯光刚亮，地铁口人流涌出。", 6),
  },
});

// Ordinary screenshot data: three products and short story settings. Element screenshots stitch incorrectly when regions exceed 1024×600 viewports; long-data cases run probes only.
const REGULAR_AD = adProject({
  title: "夏日汽水",
  brief: "面向通勤年轻人，调性轻快；说清低糖、真果汁与气泡足三个卖点。",
  products: Object.fromEntries(
    ["海盐柠檬气泡水", "白桃乌龙气泡水", "青提茉莉气泡水"].map((name) => [
      name,
      { description: "透明玻璃感罐身，罐口凝着水珠，开罐时有清晰的气泡声。" },
    ]),
  ),
  overview: {
    synopsis: "0-3 秒：冰块落进杯子。\n3-10 秒：地铁口人流涌出，主角拉开易拉罐。\n10-30 秒：三种口味依次出场，结尾领券。",
    genre: "饮品广告",
    theme: "忙碌通勤里的一口清凉",
    world_setting: "夏天傍晚的城市街角，便利店灯光刚亮。",
  },
});

const brief = (page: Page) => page.getByRole("region", { name: "创作灵感" });
const products = (page: Page) => page.getByRole("region", { name: "商品" });
const storySetting = (page: Page) => page.getByRole("region", { name: "故事设定" });

async function settled(page: Page) {
  // Fonts/cost responses change content height; wait for actual pages to load before testing scroll reachability.
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
}

async function overviewReady(page: Page) {
  await products(page).waitFor();
  await settled(page);
}

async function settingReady(page: Page) {
  await storySetting(page).waitFor();
  await settled(page);
}

defineRegionScenarios("广告项目概览与故事设定", [
  {
    name: "刚建好的广告项目：概览里直接填写创作灵感，商品区是空状态与「添加商品」",
    path: OVERVIEW_PATH,
    api: NEW_AD,
    ready: overviewReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await expect(page.getByRole("region", { name: "故事设定" })).toHaveCount(0);
      await expect(brief(page).getByRole("textbox", { name: "创作灵感" })).toHaveValue("");
      await expect(brief(page).getByRole("radio", { name: "30 秒" })).toBeChecked();
      await expect(products(page).getByRole("button", { name: "添加商品" })).toBeVisible();
    },
    screenshot: { name: "ad-overview-new-products", target: products },
  },
  {
    name: "长创作灵感与十二个商品：字段随内容撑高，由画布滚动到最后一个商品",
    path: OVERVIEW_PATH,
    api: LONG_AD,
    ready: overviewReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await expect(brief(page).getByRole("spinbutton", { name: "自定义目标总时长（秒）" })).toHaveValue("45");
      const last = products(page).getByRole("listitem").last();
      await last.scrollIntoViewIfNeeded();
      await expect(last).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "已登记三个商品：整行列出缩略图、名称与描述，标题行有「查看全部」与「添加商品」",
    path: OVERVIEW_PATH,
    api: REGULAR_AD,
    ready: overviewReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await expect(products(page).getByRole("listitem")).toHaveCount(3);
      await expect(products(page).getByRole("link", { name: "查看全部" })).toHaveAttribute(
        "href",
        "/app/projects/demo/products",
      );
    },
    screenshot: { name: "ad-overview-products", target: products },
  },
  {
    name: "创作灵感有修改时下方出现未保存提示条",
    path: OVERVIEW_PATH,
    api: LONG_AD,
    ready: overviewReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await brief(page).getByText("60 秒", { exact: true }).click();
      const save = brief(page).getByRole("button", { name: "保存" });
      await save.scrollIntoViewIfNeeded();
      await expect(save).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "创作灵感改了目标时长：提示条在区块下方",
    path: OVERVIEW_PATH,
    api: REGULAR_AD,
    ready: overviewReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await brief(page).getByText("60 秒", { exact: true }).click();
      await expect(brief(page).getByRole("button", { name: "保存" })).toBeVisible();
    },
    screenshot: { name: "ad-overview-brief-unsaved", target: brief },
  },
  {
    name: "「添加商品」从右侧打开资产详情 Sheet 的新建表单",
    path: OVERVIEW_PATH,
    api: NEW_AD,
    ready: overviewReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await products(page).getByRole("button", { name: "添加商品" }).click();
      const sheet = page.getByRole("dialog");
      await sheet.waitFor();
      await waitForEntrance(sheet);
      // Right-side Sheets align with viewport right edges.
      const viewportWidth = page.viewportSize()?.width ?? 0;
      const rect = await box(sheet);
      expect(Math.round(rect.x + rect.width)).toBe(viewportWidth);
    },
  },
  {
    name: "视频页的「故事设定」tab：排在第一个，没有批量动作，也没有「从原文生成」",
    path: SETTING_PATH,
    api: REGULAR_AD,
    ready: settingReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      const tabs = page.getByRole("tablist", { name: "集视图" }).getByRole("tab");
      await expect(tabs.first()).toHaveText("故事设定");
      await expect(tabs.first()).toHaveAttribute("aria-selected", "true");
      await expect(storySetting(page).getByRole("button", { name: /从原文/ })).toHaveCount(0);
      await expect(page.getByRole("button", { name: /补齐/ })).toHaveCount(0);
    },
    screenshot: { name: "ad-story-setting", target: storySetting },
  },
  {
    name: "「故事设定」tab 的分秒脚本式长梗概与长世界观：字段随内容撑高，由视图滚动到底",
    path: SETTING_PATH,
    api: LONG_AD,
    ready: settingReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      const world = storySetting(page).getByRole("textbox", { name: "世界观" });
      await world.scrollIntoViewIfNeeded();
      await expect(world).toBeInViewport();
    },
  },
  {
    name: "「故事设定」tab 有修改时提示条完整可见",
    path: SETTING_PATH,
    api: LONG_AD,
    ready: settingReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await storySetting(page).getByRole("textbox", { name: "主题" }).fill("一口清凉");
      const save = storySetting(page).getByRole("button", { name: "保存" });
      await save.scrollIntoViewIfNeeded();
      await expect(save).toBeInViewport({ ratio: 1 });
    },
  },
]);
