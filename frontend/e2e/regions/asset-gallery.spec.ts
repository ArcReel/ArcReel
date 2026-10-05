import type { Locator, Page } from "@playwright/test";
import { loadRecordedResponses, recordedKey } from "../support/recorded.ts";
import { clearAgentOverlay, viewport, waitForEntrance } from "../support/region-helpers.ts";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 资产画廊：工具栏（筛选、批量生成、新增）+ 按画布宽度加列的浏览卡网格；点卡片打开右侧详情 Sheet，
// 次要操作收在卡片的「更多」里。

const PATH = "/app/projects/demo/characters";

const LONG_NAME = "旧城茶馆二楼临窗座位上总在午后出现的说书先生";
const LONG_DESCRIPTION =
  "四十岁上下，瘦高，穿洗得发白的灰布长衫，袖口磨出了毛边；左眼角有一颗痣，说话时习惯用折扇轻敲桌面。每天午后准时出现在茶馆二楼临窗的位置，讲到紧要处会突然停下喝茶，让听众干着急。";

// 内联 SVG 资产图：getFileUrl 原样使用 data: 地址，不发请求。
function sheetSvg(hue: number) {
  return `data:image/svg+xml,${encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"><rect width="1280" height="720" fill="hsl(${hue} 18% 20%)"/><circle cx="640" cy="300" r="120" fill="hsl(${hue} 14% 42%)"/><rect x="460" y="440" width="360" height="200" rx="40" fill="hsl(${hue} 14% 42%)"/></svg>`,
  )}`;
}

type Character = Record<string, unknown>;

const CHARACTERS: Record<string, Character> = {
  林夕: {
    description: "二十出头的旧城茶馆老板娘，短发，常穿靛蓝布衫。",
    character_sheet: sheetSvg(220),
    aliases: ["林老板", "夕姐"],
    derivatives: { 夜行装: { description: "换上深色长袍与斗笠。" }, 便装: { description: "灰色短褂。" } },
  },
  沈砚: { description: "沉默寡言的账房先生，戴一副圆框眼镜。", character_sheet: sheetSvg(30) },
  阿岚: { description: "茶馆跑堂的少年，手脚麻利，爱打听消息。" },
  周掌柜: { description: "", character_sheet: sheetSvg(140) },
  小满: { description: "隔壁绸缎庄的学徒，常来茶馆听书。" },
};

// 产物清单：沈砚的资产图已过期，阿岚与小满待生成。
function statusRows(names: string[], stale: Set<string>, missing: Set<string>) {
  return {
    status: 200,
    body: {
      assets: names.map((name) => ({
        unit_id: `character/${name}`,
        asset_type: "character",
        name,
        derivative: null,
        status: stale.has(name) ? "stale" : missing.has(name) ? "missing" : "current",
        description_missing: !(CHARACTERS[name]?.description ?? "x"),
        image_to_image: false,
      })),
    },
  };
}

function projectWith(characters: Record<string, Character>, episodeTitles?: string[]) {
  const recorded = loadRecordedResponses().get(recordedKey("GET", "/api/v1/projects/demo"));
  if (!recorded) throw new Error("没有录制 GET /api/v1/projects/demo");
  const body = structuredClone(recorded.body) as { project: Record<string, unknown> };
  body.project.characters = characters;
  if (episodeTitles) {
    // 以录制的第 1 集为模板，按标题铺出多集。
    const [template] = body.project.episodes as Record<string, unknown>[];
    body.project.episodes = episodeTitles.map((title, i) => ({
      ...template,
      episode: i + 1,
      title,
      script_file: `scripts/episode_${i + 1}.json`,
    }));
  }
  return { status: recorded.status, body };
}

// 小满有一个进行中的资产图任务，卡片显示「生成中」。
const RUNNING_TASKS = {
  status: 200,
  body: {
    items: [
      {
        task_id: "task-xiaoman",
        project_name: "demo",
        task_type: "character",
        media_type: "image",
        resource_id: "小满",
        resource_type: null,
        script_file: null,
        payload: {},
        status: "running",
        result: null,
        error_message: null,
        cancelled_by: null,
        provider_id: null,
        provider_job_id: null,
        source: "webui",
        queued_at: "2026-04-20T00:00:00Z",
        started_at: "2026-04-20T00:00:01Z",
        finished_at: null,
        updated_at: "2026-04-20T00:00:01Z",
      },
    ],
    total: 1,
    page: 1,
    page_size: 200,
  },
};

// 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，画廊停在替换的项目数据上。
const BASE: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
  "GET /api/v1/projects/demo": projectWith(CHARACTERS),
  "GET /api/v1/projects/demo/asset-sheets/status": statusRows(
    Object.keys(CHARACTERS),
    new Set(["沈砚"]),
    new Set(["阿岚", "小满"]),
  ),
  "GET /api/v1/tasks?page_size=200&project_name=demo": RUNNING_TASKS,
  "GET /api/v1/projects/demo/versions/characters/林夕": {
    status: 200,
    body: {
      resource_type: "characters",
      resource_id: "林夕",
      current_version: 3,
      versions: [1, 2, 3].map((version) => ({
        version,
        filename: `林夕_v${version}.png`,
        created_at: `2026-02-0${version} 21:3${version}`,
        file_size: 1024,
        is_current: version === 3,
        prompt: version === 1 ? LONG_DESCRIPTION : `第 ${version} 版资产图`,
        file_url: sheetSvg(version * 70),
        source: version === 2 ? "image_edit" : "generate",
      })),
    },
  },
};

// 压力变体：40 个名字与描述都很长、带别名与衍生的角色。
const MANY: Record<string, Character> = Object.fromEntries(
  Array.from({ length: 40 }, (_, i) => [
    `${LONG_NAME} ${i + 1}`,
    {
      description: LONG_DESCRIPTION,
      character_sheet: i % 3 === 0 ? undefined : sheetSvg(i * 9),
      aliases: ["说书先生", "老先生", "先生"],
      derivatives: Object.fromEntries(Array.from({ length: 12 }, (_, d) => [`外观 ${d + 1}`, { description: "换装" }])),
    },
  ]),
);
const MANY_API: ApiOverrides = {
  ...BASE,
  "GET /api/v1/projects/demo": projectWith(MANY),
  "GET /api/v1/projects/demo/asset-sheets/status": { status: 200, body: { assets: [] } },
  "GET /api/v1/tasks?page_size=200&project_name=demo": { status: 200, body: { items: [], total: 0, page: 1, page_size: 200 } },
};

// 删除前的引用预览（dry_run）：林夕被前三集引用。
const DELETE_PREVIEW_KEY = "DELETE /api/v1/projects/demo/characters/林夕?dry_run=true";
const DELETE_API: ApiOverrides = {
  ...BASE,
  "GET /api/v1/projects/demo": projectWith(CHARACTERS, ["茶馆开张", "夜访账房", "说书先生"]),
  [DELETE_PREVIEW_KEY]: {
    status: 200,
    body: {
      success: true,
      dry_run: true,
      name: "林夕",
      references: 9,
      episodes: [
        { episode: 1, references: 4 },
        { episode: 2, references: 3 },
        { episode: 3, references: 2 },
      ],
    },
  },
};
// 压力变体：被 30 集引用，集标题很长。
const MANY_EPISODES = Array.from({ length: 30 }, (_, i) => `${LONG_NAME}的第 ${i + 1} 段故事`);
const DELETE_MANY_API: ApiOverrides = {
  ...BASE,
  "GET /api/v1/projects/demo": projectWith(CHARACTERS, MANY_EPISODES),
  [DELETE_PREVIEW_KEY]: {
    status: 200,
    body: {
      success: true,
      dry_run: true,
      name: "林夕",
      references: 30 * 12,
      episodes: MANY_EPISODES.map((_, i) => ({ episode: i + 1, references: 12 })),
    },
  },
};

async function openDeleteDialog(page: Page): Promise<Locator> {
  const menu = await openMenu(page, "林夕");
  await menu.getByRole("menuitem", { name: "删除" }).click();
  const dialog = page.getByRole("alertdialog", { name: "删除角色「林夕」？" });
  await dialog.waitFor();
  await waitForEntrance(dialog);
  return dialog;
}

function grid(page: Page) {
  return page.getByRole("list", { name: "角色" });
}

function cards(page: Page) {
  return grid(page).getByRole("article");
}

async function galleryReady(page: Page) {
  await expect(cards(page).first()).toBeVisible();
  // 等产物清单回来：沈砚的「已过期」标记来自它。
  await expect(page.getByRole("button", { name: /^已过期/ })).toContainText("1");
}

async function openMenu(page: Page, name: string): Promise<Locator> {
  await clearAgentOverlay(page);
  await page.getByRole("button", { name: `「${name}」的更多操作` }).click();
  const menu = page.getByRole("menu");
  await menu.waitFor();
  await waitForEntrance(menu);
  return menu;
}

async function columnCount(page: Page) {
  const lefts = await cards(page).evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().left)));
  return new Set(lefts).size;
}

defineRegionScenarios("资产画廊", [
  {
    name: "浏览卡网格按画布宽度加列，状态标记在图片左上角",
    path: PATH,
    api: BASE,
    ready: galleryReady,
    act: async (page) => {
      const xiaoman = page.getByRole("article", { name: "小满" });
      await expect(xiaoman.getByText("生成中")).toBeVisible();
      await expect(page.getByRole("article", { name: "周掌柜" }).getByText("缺描述")).toBeVisible();
      await expect(page.getByRole("article", { name: "阿岚" }).getByText("待生成")).toBeVisible();
      // 1440 宽、Agent 面板展开时，角色网格是两列。
      if (viewport(page).width === 1440) {
        await expect(page.getByRole("button", { name: "Agent", exact: true })).toHaveAttribute("aria-pressed", "true");
        expect(await columnCount(page)).toBe(2);
      }
    },
    screenshot: { name: "asset-gallery", target: (page) => page.getByRole("region", { name: "角色" }) },
  },
  {
    name: "条目多且名字与描述很长：卡片不撑破网格，工具栏留在顶部",
    path: PATH,
    api: MANY_API,
    ready: async (page) => {
      await expect(cards(page)).toHaveCount(40);
    },
    act: async (page) => {
      const last = page.getByRole("article", { name: `${LONG_NAME} 40` });
      await last.scrollIntoViewIfNeeded();
      await expect(last).toBeInViewport();
      await expect(page.getByRole("button", { name: "添加角色" })).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "工具栏筛选只留待生成或已过期的资产",
    path: PATH,
    api: BASE,
    ready: galleryReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await page.getByRole("button", { name: /^待生成/ }).click();
      await expect(cards(page)).toHaveCount(2);
      await page.getByRole("button", { name: /^已过期/ }).click();
      await expect(cards(page)).toHaveCount(1);
      await expect(page.getByRole("article", { name: "沈砚" })).toBeVisible();
    },
  },
  {
    name: "打开卡片的「更多」菜单",
    path: PATH,
    api: BASE,
    ready: galleryReady,
    act: async (page) => {
      const menu = await openMenu(page, "林夕");
      await expect(menu.getByRole("menuitem", { name: "并入…" })).toBeVisible();
    },
    screenshot: { name: "asset-gallery-menu", target: (page) => page.getByRole("menu") },
  },
  {
    name: "从菜单打开版本历史",
    path: PATH,
    api: BASE,
    ready: galleryReady,
    act: async (page) => {
      const menu = await openMenu(page, "林夕");
      await menu.getByRole("menuitem", { name: "版本历史" }).click();
      const popover = page.getByRole("dialog", { name: "历史版本" });
      await popover.waitFor();
      await waitForEntrance(popover);
      await expect(popover.getByRole("button", { name: "v3" })).toBeVisible();
    },
    screenshot: { name: "asset-gallery-versions", target: (page) => page.getByRole("dialog", { name: "历史版本" }) },
  },
  {
    name: "从菜单打开局部修改",
    path: PATH,
    api: BASE,
    ready: galleryReady,
    act: async (page) => {
      const menu = await openMenu(page, "林夕");
      await menu.getByRole("menuitem", { name: "局部修改" }).click();
      const dialog = page.getByRole("dialog", { name: "局部修改" });
      await dialog.waitFor();
      await waitForEntrance(dialog);
    },
  },
  {
    name: "从菜单打开「并入…」",
    path: PATH,
    api: BASE,
    ready: galleryReady,
    act: async (page) => {
      const menu = await openMenu(page, "林夕");
      await menu.getByRole("menuitem", { name: "并入…" }).click();
      const dialog = page.getByRole("alertdialog");
      await dialog.waitFor();
      await waitForEntrance(dialog);
      await expect(dialog.getByRole("button", { name: "取消" })).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "点卡片打开详情 Sheet",
    path: PATH,
    api: BASE,
    ready: galleryReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await page.getByRole("button", { name: "林夕", exact: true }).click();
      const sheet = page.getByRole("dialog", { name: "林夕" });
      await sheet.waitFor();
      await waitForEntrance(sheet);
      await expect(sheet.getByRole("button", { name: "关闭" })).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "删除有引用的资产：确认框写明被哪些集引用，并可改为并入",
    path: PATH,
    api: DELETE_API,
    ready: galleryReady,
    act: async (page) => {
      const dialog = await openDeleteDialog(page);
      await expect(dialog.getByText(/^被「茶馆开张」等 3 集共 9 处引用/)).toBeVisible();
      await expect(dialog.getByRole("button", { name: "改为并入…" })).toBeVisible();
      await expect(dialog.getByRole("button", { name: "删除" })).toBeEnabled();
    },
    screenshot: { name: "asset-gallery-delete", target: (page) => page.getByRole("alertdialog") },
  },
  {
    name: "删除被很多集引用的资产：只有影响清单滚动，按钮留在视野里",
    path: PATH,
    api: DELETE_MANY_API,
    ready: galleryReady,
    act: async (page) => {
      const dialog = await openDeleteDialog(page);
      const list = dialog.getByRole("region", { name: "删除角色「林夕」？" });
      await expect(list.getByRole("listitem")).toHaveCount(30);
      await expect(dialog.getByRole("button", { name: "删除" })).toBeInViewport({ ratio: 1 });
      await expect(dialog.getByRole("button", { name: "改为并入…" })).toBeInViewport({ ratio: 1 });
    },
  },
]);
