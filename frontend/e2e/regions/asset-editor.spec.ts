import type { Locator, Page } from "@playwright/test";
import { loadRecordedResponses, recordedKey } from "../support/recorded.ts";
import { clearAgentOverlay, waitForEntrance } from "../support/region-helpers.ts";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 资产详情 Sheet：点浏览卡打开的右侧编辑器，四类资产共用。头部改名与上一个、下一个，正文按类型
// 开关字段区块，底部是未保存修改的内联提示条；「添加」在同一个 Sheet 里打开空白表单。

const CHARACTERS_PATH = "/app/projects/demo/characters";
const PRODUCTS_PATH = "/app/projects/demo/products";

const LONG_NAME = "旧城茶馆二楼临窗座位上总在午后出现的说书先生";
const LONG_DESCRIPTION = Array.from(
  { length: 6 },
  () =>
    "四十岁上下，瘦高，穿洗得发白的灰布长衫，袖口磨出了毛边；左眼角有一颗痣，说话时习惯用折扇轻敲桌面。每天午后准时出现在茶馆二楼临窗的位置，讲到紧要处会突然停下喝茶，让听众干着急。",
).join("");

// 内联 SVG 图片：getFileUrl 原样使用 data: 地址，不发请求。
function svg(hue: number, width = 1280, height = 720) {
  return `data:image/svg+xml,${encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="${width}" height="${height}" fill="hsl(${hue} 18% 20%)"/><circle cx="${width / 2}" cy="${height * 0.42}" r="${height / 6}" fill="hsl(${hue} 14% 42%)"/></svg>`,
  )}`;
}

type Asset = Record<string, unknown>;

const CHARACTERS: Record<string, Asset> = {
  林夕: {
    description: "二十出头的旧城茶馆老板娘，短发，常穿靛蓝布衫。",
    voice_style: "爽朗，语速偏快",
    character_sheet: svg(220),
    reference_image: svg(200, 720, 960),
    aliases: ["林老板", "夕姐"],
    derivatives: { 夜行装: { description: "换上深色长袍与斗笠。" } },
  },
  沈砚: { description: "沉默寡言的账房先生，戴一副圆框眼镜。", character_sheet: svg(30) },
  阿岚: { description: "茶馆跑堂的少年，手脚麻利，爱打听消息。" },
};

const PRODUCTS: Record<string, Asset> = {
  竹叶青茶: {
    description: "明前绿茶，扁平挺直的芽叶，冲泡后汤色清亮。",
    brand: "旧城茶馆",
    selling_points: ["明前头采", "高山茶园", "独立小罐装"],
    product_sheet: svg(110),
    reference_images: [svg(100, 800, 800), svg(120, 800, 800), svg(90, 800, 800)],
  },
};

function projectWith(fields: Record<string, unknown>) {
  const recorded = loadRecordedResponses().get(recordedKey("GET", "/api/v1/projects/demo"));
  if (!recorded) throw new Error("没有录制 GET /api/v1/projects/demo");
  const body = structuredClone(recorded.body) as { project: Record<string, unknown> };
  Object.assign(body.project, fields);
  return { status: recorded.status, body };
}

function statusRows(type: string, names: string[]) {
  return {
    status: 200,
    body: {
      assets: names.map((name) => ({
        unit_id: `${type}/${name}`,
        asset_type: type,
        name,
        derivative: null,
        status: "current",
        description_missing: false,
        image_to_image: false,
      })),
    },
  };
}

const NO_TASKS = { status: 200, body: { items: [], total: 0, page: 1, page_size: 200 } };

// 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，Sheet 停在替换的项目数据上。
const BASE: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
  "GET /api/v1/projects/demo": projectWith({ characters: CHARACTERS }),
  "GET /api/v1/projects/demo/asset-sheets/status": statusRows("character", Object.keys(CHARACTERS)),
  "GET /api/v1/tasks?page_size=200&project_name=demo": NO_TASKS,
};

const PRODUCT_API: ApiOverrides = {
  ...BASE,
  "GET /api/v1/projects/demo": projectWith({ products: PRODUCTS }),
  "GET /api/v1/projects/demo/asset-sheets/status": statusRows("product", Object.keys(PRODUCTS)),
};

// 压力变体：名字与描述很长、别名很多，项目用参考音频绑定声音。
const LONG_API: ApiOverrides = {
  ...BASE,
  "GET /api/v1/projects/demo": projectWith({
    character_voice_binding: "reference_audio",
    characters: {
      [LONG_NAME]: {
        description: LONG_DESCRIPTION,
        voice_style: "低沉沙哑，讲到紧要处会刻意放慢",
        character_sheet: svg(40),
        reference_image: svg(50, 720, 960),
        aliases: Array.from({ length: 24 }, (_, i) => `说书先生的第 ${i + 1} 个称呼`),
      },
      沈砚: CHARACTERS.沈砚,
    },
  }),
  "GET /api/v1/projects/demo/asset-sheets/status": statusRows("character", [LONG_NAME, "沈砚"]),
};

async function galleryReady(page: Page) {
  await expect(page.getByRole("list", { name: /角色|商品/ }).getByRole("article").first()).toBeVisible();
}

async function openSheet(page: Page, name: string): Promise<Locator> {
  await clearAgentOverlay(page);
  await page.getByRole("button", { name, exact: true }).click();
  const sheet = page.getByRole("dialog", { name });
  await sheet.waitFor();
  await waitForEntrance(sheet);
  return sheet;
}

async function editDescription(sheet: Locator, text: string) {
  await sheet.getByRole("textbox", { name: "描述" }).fill(text);
  await expect(sheet.getByText("有未保存的修改")).toBeVisible();
}

defineRegionScenarios("资产详情 Sheet", [
  {
    name: "打开角色详情：资产图、原图、描述、声音、别名与衍生",
    path: CHARACTERS_PATH,
    api: BASE,
    ready: galleryReady,
    act: async (page) => {
      const sheet = await openSheet(page, "林夕");
      await expect(sheet.getByRole("button", { name: "重新生成资产图" })).toBeVisible();
      await expect(sheet.getByRole("button", { name: "上一个" })).toBeDisabled();
      await expect(sheet.getByRole("button", { name: "下一个" })).toBeEnabled();
      await expect(sheet.getByRole("button", { name: "关闭" })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "asset-editor", target: (page) => page.getByRole("dialog", { name: "林夕" }) },
  },
  {
    name: "改了描述：底部出现未保存提示条，生成与预览都先保存",
    path: CHARACTERS_PATH,
    api: BASE,
    ready: galleryReady,
    act: async (page) => {
      const sheet = await openSheet(page, "林夕");
      await editDescription(sheet, "二十出头的旧城茶馆老板娘，短发，今天换了一身红衣。");
      await expect(sheet.getByRole("button", { name: "保存并生成" })).toBeVisible();
      await expect(sheet.getByRole("button", { name: "保存并预览" })).toBeVisible();
      await expect(sheet.getByRole("button", { name: "保存", exact: true })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "asset-editor-unsaved", target: (page) => page.getByRole("dialog", { name: "林夕" }) },
  },
  {
    name: "有未保存修改时切到下一个：先弹出离开拦截",
    path: CHARACTERS_PATH,
    api: BASE,
    ready: galleryReady,
    act: async (page) => {
      const sheet = await openSheet(page, "林夕");
      await editDescription(sheet, "改过的描述");
      await sheet.getByRole("button", { name: "下一个" }).click();
      const leave = page.getByRole("alertdialog", { name: "「林夕」有未保存的修改" });
      await leave.waitFor();
      await waitForEntrance(leave);
      await expect(leave.getByRole("button", { name: "保存并切换" })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "asset-editor-leave", target: (page) => page.getByRole("alertdialog") },
  },
  {
    name: "离开拦截里点「继续编辑」：回到原资产，修改还在",
    path: CHARACTERS_PATH,
    api: BASE,
    ready: galleryReady,
    act: async (page) => {
      const sheet = await openSheet(page, "林夕");
      await editDescription(sheet, "改过的描述");
      await sheet.getByRole("button", { name: "关闭" }).click();
      const leave = page.getByRole("alertdialog", { name: "「林夕」有未保存的修改" });
      await leave.waitFor();
      await leave.getByRole("button", { name: "继续编辑" }).click();
      await expect(leave).toBeHidden();
      await expect(sheet).toBeVisible();
      await expect(sheet.getByRole("textbox", { name: "描述" })).toHaveValue("改过的描述");
    },
  },
  {
    name: "添加角色：在 Sheet 里打开空白表单",
    path: CHARACTERS_PATH,
    api: BASE,
    ready: galleryReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await page.getByRole("button", { name: "添加角色" }).click();
      const form = page.getByRole("dialog", { name: "添加角色" });
      await form.waitFor();
      await waitForEntrance(form);
      await expect(form.getByRole("button", { name: "创建" })).toBeDisabled();
      await expect(form.getByRole("button", { name: "创建" })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "asset-editor-create", target: (page) => page.getByRole("dialog", { name: "添加角色" }) },
  },
  {
    name: "商品详情：商品原图、品牌与卖点",
    path: PRODUCTS_PATH,
    api: PRODUCT_API,
    ready: galleryReady,
    act: async (page) => {
      const sheet = await openSheet(page, "竹叶青茶");
      await expect(sheet.getByRole("button", { name: /^查看大图：「竹叶青茶」的商品原图/ })).toHaveCount(3);
      await expect(sheet.getByRole("textbox", { name: "卖点" })).toHaveValue("明前头采\n高山茶园\n独立小罐装");
    },
  },
  {
    name: "名字与描述很长、别名很多：只有正文滚动，提示条留在底部",
    path: CHARACTERS_PATH,
    api: LONG_API,
    ready: galleryReady,
    act: async (page) => {
      const sheet = await openSheet(page, LONG_NAME);
      await editDescription(sheet, `${LONG_DESCRIPTION}（改）`);
      const add = sheet.getByRole("button", { name: "添加别名" });
      await add.scrollIntoViewIfNeeded();
      await expect(add).toBeInViewport({ ratio: 1 });
      await expect(sheet.getByRole("button", { name: "保存", exact: true })).toBeInViewport({ ratio: 1 });
      await expect(sheet.getByRole("button", { name: "关闭" })).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "打开「添加别名」弹层",
    path: CHARACTERS_PATH,
    api: BASE,
    ready: galleryReady,
    act: async (page) => {
      const sheet = await openSheet(page, "林夕");
      const trigger = sheet.getByRole("button", { name: "添加别名" });
      await trigger.scrollIntoViewIfNeeded();
      await trigger.click();
      const popover = page.getByRole("dialog").filter({ has: page.getByRole("textbox", { name: "别名" }) });
      await popover.waitFor();
      await waitForEntrance(popover);
      await expect(popover.getByRole("textbox", { name: "别名" })).toBeFocused();
      await expect(sheet).toBeVisible();
    },
  },
  {
    name: "查看资产图大图：在 Sheet 之上打开，关闭后回到 Sheet",
    path: CHARACTERS_PATH,
    api: BASE,
    ready: galleryReady,
    act: async (page) => {
      const sheet = await openSheet(page, "林夕");
      await sheet.getByRole("button", { name: "查看大图：「林夕」的资产图" }).click();
      const viewer = page.getByRole("dialog", { name: "林夕" }).last();
      await waitForEntrance(viewer);
      await expect(viewer.getByRole("img", { name: "「林夕」的资产图" })).toBeVisible();
      await viewer.getByRole("button", { name: "关闭" }).click();
      await expect(page.getByRole("dialog", { name: "林夕" })).toHaveCount(1);
      await expect(sheet.getByRole("textbox", { name: "描述" })).toBeVisible();
    },
  },
]);
