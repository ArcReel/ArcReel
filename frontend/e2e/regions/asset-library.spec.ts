import type { Locator, Page } from "@playwright/test";
import { loadRecordedResponses, recordedKey } from "../support/recorded.ts";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 资产库：铺满档的页面外壳，类型标签带计数，网格滚动到底自动加载下一页；点卡片打开右侧详情 Sheet。
// 项目画廊里的「从资产库导入」选择器与「加入资产库」预览对话框也在这里探测（画廊本体尚未重做）。

const LIBRARY_PATH = "/app/assets";
const GALLERY_PATH = "/app/projects/demo/characters";
const FIRST_PAGE = "GET /api/v1/assets?limit=60&type=character";
const SECOND_PAGE = "GET /api/v1/assets?limit=60&offset=60&type=character";
const UPDATED_AT = "2025-12-28T06:30:00Z";

const LONG_NAME = "旧城茶馆二楼临窗座位上总在午后出现的说书先生";
const LONG_DESCRIPTION =
  "四十岁上下，瘦高，穿洗得发白的灰布长衫，袖口磨出了毛边；左眼角有一颗痣，说话时习惯用折扇轻敲桌面。每天午后准时出现在茶馆二楼临窗的位置，讲到紧要处会突然停下喝茶，让听众干着急。年轻时据说在码头扛过包，后来不知怎么成了说书人，没人知道他的真名。";

// 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，画廊停在录制的项目数据上。
const EVENT_STREAM: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

function asset(index: number, overrides: Record<string, unknown> = {}) {
  return {
    id: `00000000-0000-4000-9000-${String(index).padStart(12, "0")}`,
    type: "character",
    name: `${LONG_NAME} ${index}`,
    description: LONG_DESCRIPTION,
    voice_style: "低沉略带沙哑，讲到紧要处语速放慢",
    image_path: null,
    audio_path: null,
    source_project: "demo",
    updated_at: UPDATED_AT,
    derivatives: [],
    ...overrides,
  };
}

function listPage(items: unknown[], total: number) {
  return { status: 200, body: { items, total, counts: { character: total, scene: 1280, prop: 36 } } };
}

// 压力变体：75 个名字与描述都很长的角色，分两页返回。
const MANY_ASSETS: ApiOverrides = {
  [FIRST_PAGE]: listPage(
    Array.from({ length: 60 }, (_, i) => asset(i + 1)),
    75,
  ),
  [SECOND_PAGE]: listPage(
    Array.from({ length: 15 }, (_, i) => asset(i + 61)),
    75,
  ),
};

// 会撑高的详情：长描述、参考音频与 8 个衍生。
const RICH_ASSET = asset(1, {
  name: LONG_NAME,
  audio_path: "global_assets/character/voice.wav",
  derivatives: Array.from({ length: 8 }, (_, i) => ({
    name: `衍生外观 ${i + 1}`,
    description: "换上深色长袍与斗笠，腰间多一只酒葫芦，其余保持不变；夜里出场时提一盏纸灯笼。",
    image_path: null,
  })),
});
const RICH_DETAIL: ApiOverrides = { [FIRST_PAGE]: listPage([RICH_ASSET], 1) };

/**
 * 等弹层的进场过渡结束再探测：淡入途中的半透明文字会被 axe 判为对比度不足。
 * 过渡被重渲染取消时 `finished` 会以 AbortError 拒绝，取消也算结束。
 */
async function settled(locator: Locator) {
  await locator.evaluate((el) => Promise.allSettled(el.getAnimations({ subtree: true }).map((a) => a.finished)));
}

async function libraryReady(page: Page) {
  await page.getByRole("tab", { name: /角色/ }).waitFor();
  await page.getByRole("list").first().waitFor();
}

async function openDetail(page: Page, name: string): Promise<Locator> {
  await page.getByRole("button", { name, exact: true }).click();
  const sheet = page.getByRole("dialog", { name });
  await sheet.waitFor();
  await settled(sheet);
  return sheet;
}

// 内联 SVG 资产图：getFileUrl 原样使用 data: 地址，不发请求。画廊卡片没有资产图时的占位会被裁切，
// 那是画廊本体的问题，这里给角色一张图避开。
const SHEET_SVG = `data:image/svg+xml,${encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"><rect width="1280" height="720" fill="#2a2d3a"/><circle cx="640" cy="300" r="120" fill="#5b5f7a"/><rect x="460" y="440" width="360" height="200" rx="40" fill="#5b5f7a"/></svg>',
)}`;

// 画廊里有一个角色，供「加入资产库」预览。
function projectWithCharacter() {
  const recorded = loadRecordedResponses().get(recordedKey("GET", "/api/v1/projects/demo"));
  if (!recorded) throw new Error("没有录制 GET /api/v1/projects/demo");
  const body = structuredClone(recorded.body) as { project: Record<string, unknown> };
  body.project.characters = {
    林夕: {
      description: "二十出头的旧城茶馆老板娘，短发，常穿靛蓝布衫。",
      character_sheet: SHEET_SVG,
      voice_style: "温和、语速偏慢",
      reference_audio: "characters/refs_audio/林夕.wav",
      derivatives: { 夜行装: { description: "换上深色长袍与斗笠。" }, 便装: { description: "灰色短褂。" } },
    },
  };
  return { status: recorded.status, body };
}

async function galleryReady(page: Page) {
  await page.getByRole("button", { name: "从资产库选择" }).waitFor();
}

defineRegionScenarios("资产库", [
  {
    name: "类型标签显示当前搜索下各类型的数量，网格铺满内容区",
    path: LIBRARY_PATH,
    ready: libraryReady,
    act: async (page) => {
      await expect(page.getByRole("tab", { name: /场景\s*1/ })).toBeVisible();
      await expect(page.getByRole("button", { name: "林夕", exact: true })).toBeVisible();
    },
    screenshot: { name: "asset-library", target: (page) => page.getByRole("main") },
  },
  {
    name: "条目多且文字长时，滚动到底自动加载下一页",
    path: LIBRARY_PATH,
    api: MANY_ASSETS,
    ready: libraryReady,
    act: async (page) => {
      await expect(page.getByRole("tab", { name: /场景\s*1280/ })).toBeVisible();
      await page.getByRole("button", { name: `${LONG_NAME} 60`, exact: true }).scrollIntoViewIfNeeded();
      const last = page.getByRole("button", { name: `${LONG_NAME} 75`, exact: true });
      await expect(last).toBeAttached();
      await last.scrollIntoViewIfNeeded();
      await expect(last).toBeInViewport();
    },
  },
  {
    name: "详情 Sheet 内容撑高时只有正文滚动，标题与底部操作留在视口内",
    path: LIBRARY_PATH,
    api: RICH_DETAIL,
    ready: libraryReady,
    act: async (page) => {
      const sheet = await openDetail(page, LONG_NAME);
      const edit = sheet.getByRole("button", { name: "编辑" });
      const close = sheet.getByRole("button", { name: "关闭" });
      await expect(edit).toBeInViewport({ ratio: 1 });
      await expect(close).toBeInViewport({ ratio: 1 });

      const last = sheet.getByText("衍生外观 8");
      await last.scrollIntoViewIfNeeded();
      await expect(last).toBeInViewport();
      await expect(edit).toBeInViewport({ ratio: 1 });
      await expect(close).toBeInViewport({ ratio: 1 });
      await sheet.getByRole("heading", { name: LONG_NAME }).scrollIntoViewIfNeeded();
    },
    screenshot: { name: "asset-library-detail", target: (page) => page.getByRole("dialog") },
  },
  {
    name: "在详情里进入编辑后，保存栏固定在 Sheet 底部",
    path: LIBRARY_PATH,
    api: RICH_DETAIL,
    ready: libraryReady,
    act: async (page) => {
      const sheet = await openDetail(page, LONG_NAME);
      await sheet.getByRole("button", { name: "编辑" }).click();
      const description = sheet.getByLabel("描述");
      await description.fill(`${LONG_DESCRIPTION}\n\n${LONG_DESCRIPTION}\n\n${LONG_DESCRIPTION}`);
      const save = sheet.getByRole("button", { name: "保存" });
      await expect(save).toBeEnabled();
      await expect(save).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "从卡片菜单打开「应用到项目」对话框",
    path: LIBRARY_PATH,
    ready: libraryReady,
    act: async (page) => {
      await page.getByRole("button", { name: "「林夕」的更多操作" }).click();
      await page.getByRole("menuitem", { name: "应用到项目…" }).click();
      const dialog = page.getByRole("dialog", { name: "把「林夕」应用到项目" });
      await dialog.waitFor();
      await settled(dialog);
      await expect(dialog.getByRole("button", { name: "应用", exact: true })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "asset-library-apply", target: (page) => page.getByRole("dialog") },
  },
  {
    name: "画廊「从资产库导入」：条目多时只有正文滚动，显示匹配总数",
    path: GALLERY_PATH,
    api: { ...EVENT_STREAM, ...MANY_ASSETS },
    ready: galleryReady,
    act: async (page) => {
      // 紧凑档的 Agent 面板默认展开并盖住画布右侧，先收起面板再点画布右上角的按钮
      await page.getByRole("button", { name: "Agent", exact: true }).click();
      await page.getByRole("button", { name: "从资产库选择" }).click();
      const dialog = page.getByRole("dialog", { name: "从资产库选择角色" });
      await dialog.waitFor();
      await settled(dialog);
      await expect(dialog.getByText("匹配 75 个角色")).toBeVisible();
      const last = dialog.getByRole("button", { name: new RegExp(`${LONG_NAME} 75`) });
      await dialog.getByRole("button", { name: new RegExp(`${LONG_NAME} 60`) }).scrollIntoViewIfNeeded();
      await expect(last).toBeAttached();
      await last.scrollIntoViewIfNeeded();
      await expect(dialog.getByRole("button", { name: "取消" })).toBeInViewport({ ratio: 1 });
      await dialog.getByRole("searchbox").scrollIntoViewIfNeeded();
    },
    screenshot: { name: "asset-library-picker", target: (page) => page.getByRole("dialog") },
  },
  {
    name: "画廊「加入资产库」：只读预览，重名时提供覆盖",
    path: GALLERY_PATH,
    api: {
      ...EVENT_STREAM,
      "GET /api/v1/projects/demo": projectWithCharacter(),
      // 角色卡的声音试听会请求参考音频；页面级套件不回放媒体文件。
      "GET /api/v1/files/demo/characters/refs_audio/林夕.wav": { status: 404, body: { detail: "页面级套件不回放媒体" } },
    },
    ready: galleryReady,
    act: async (page) => {
      // 画廊角色卡的资产图框比内容矮几像素，属于尚未重做的画廊本体，这里只豁免这一个元素，其余照常探测。
      await page.evaluate(() => {
        document
          .getElementById("character-林夕")
          ?.querySelector(".aspect-video")
          ?.setAttribute("data-overflow-ok", "画廊角色卡的资产图框，随画廊重做修复");
      });
      await page.getByRole("button", { name: "加入资产库" }).click();
      const dialog = page.getByRole("dialog", { name: "加入资产库：林夕" });
      await dialog.waitFor();
      await settled(dialog);
      await expect(dialog.getByRole("button", { name: "覆盖已有" })).toBeInViewport({ ratio: 1 });
      await expect(dialog.getByText("2 个衍生")).toBeVisible();
    },
    screenshot: { name: "asset-library-add-preview", target: (page) => page.getByRole("dialog") },
  },
  {
    name: "删除失败原因很长：正文可聚焦并用键盘滚动，取消仍可达",
    path: LIBRARY_PATH,
    ready: libraryReady,
    api: { "DELETE /api/v1/assets/00000000-0000-4000-8000-000000000002": {
      status: 500, body: { detail: "无法删除资产，存储系统返回诊断：".repeat(120) },
    } },
    act: async (page) => {
      await page.getByRole("button", { name: "「林夕」的更多操作" }).click();
      await page.getByRole("menuitem", { name: "删除" }).click();
      const dialog = page.getByRole("alertdialog", { name: "删除角色「林夕」？" });
      await dialog.getByRole("button", { name: "删除", exact: true }).click();
      await expect(dialog.getByRole("alert")).toContainText("无法删除资产");
      const body = dialog.getByRole("region", { name: "删除角色「林夕」？" });
      await body.focus();
      await body.press("End");
      await expect.poll(() => body.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
      await expect(dialog.getByRole("button", { name: "取消" })).toBeInViewport({ ratio: 1 });
      await settled(dialog);
    },
  },
]);
