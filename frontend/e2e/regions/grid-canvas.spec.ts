import { box, clearAgentOverlay } from "../support/region-helpers.ts";
import type { Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { FIXED_NOW, recorded } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 集页「多宫格分镜图」视图：按章节切分点分组，每组一张卡片，信息栏加联合图媒体栏。
const GRID_PATH = "/app/projects/demo/episodes/1?view=grid";


interface RecordedProject {
  project: Record<string, unknown>;
  scripts: Record<string, Record<string, unknown>>;
}

const project = recorded<RecordedProject>("project-demo.json");
const recordedScript = project.scripts["episode_1.json"];

const shotId = (n: number) => `E1S${String(n).padStart(2, "0")}`;

// 四组：9、14、4、6 个分镜。第 2 组超过单张 9 格的上限，切成两张联合图。
const GROUP_SIZES = [9, 14, 4, 6];
const GROUPS: string[][] = [];
{
  let next = 1;
  for (const size of GROUP_SIZES) {
    GROUPS.push(Array.from({ length: size }, () => shotId(next++)));
  }
}

const segments = GROUPS.flatMap((group) =>
  group.map((id, index) => ({
    segment_id: id,
    episode: 1,
    duration_seconds: 4,
    segment_break: index === 0,
    novel_text: `${id} 的原文`,
    characters_in_segment: [],
    image_prompt: `${id} 的分镜图提示词`,
    video_prompt: `${id} 的视频提示词`,
  })),
);

// 联合图与参考图用内联 SVG：getFileUrl 原样使用 data: 地址，不发请求，截图稳定。
function compositeSvg(rows: number, cols: number, hue: number) {
  const cellW = 90;
  const cellH = 160;
  const cells = Array.from({ length: rows * cols }, (_, i) => {
    const x = (i % cols) * cellW;
    const y = Math.floor(i / cols) * cellH;
    const light = 28 + ((i * 7) % 24);
    return `<rect x="${x + 2}" y="${y + 2}" width="${cellW - 4}" height="${cellH - 4}" fill="hsl(${hue} 40% ${light}%)"/>`;
  }).join("");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${cols * cellW * 4}" height="${rows * cellH * 4}" viewBox="0 0 ${cols * cellW} ${rows * cellH}"><rect width="100%" height="100%" fill="#111"/>${cells}</svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

function refSvg(hue: number) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="hsl(${hue} 35% 40%)"/><circle cx="32" cy="26" r="12" fill="hsl(${hue} 35% 70%)"/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

const REFERENCES = [
  { name: "林夕", ref_type: "character" },
  { name: "陈默", ref_type: "character" },
  { name: "旧城茶馆二楼临街的雕花木窗与窗下那张总被老主顾占着的方桌", ref_type: "scene" },
  { name: "青铜罗盘", ref_type: "prop" },
  { name: "巡夜人提着的那盏蒙着油纸、灯芯总也剪不齐的旧马灯", ref_type: "prop" },
  { name: "箭楼", ref_type: "scene" },
  { name: "林夕的母亲（回忆）", ref_type: "character" },
  { name: "修表铺", ref_type: "scene" },
].map((ref, i) => ({ ...ref, path: refSvg(i * 40) }));

const LONG_ERROR =
  "图像供应商返回错误：请求的联合图包含 9 个分镜，提示词合计超出模型单次输入上限（约 32k token），请减少这一组的分镜数量，或缩短各分镜图提示词后重新生成。";

function grid(
  id: string,
  sceneIds: string[],
  overrides: Record<string, unknown> & { rows: number; cols: number },
) {
  return {
    id,
    episode: 1,
    script_file: "episode_1.json",
    scene_ids: sceneIds,
    grid_image_path: compositeSvg(overrides.rows, overrides.cols, id.length * 37),
    cell_count: overrides.rows * overrides.cols,
    frame_chain: [],
    status: "completed",
    prompt: null,
    provider: "gemini-aistudio",
    model: "gemini-3-pro-image-preview",
    grid_size: "grid_9",
    created_at: FIXED_NOW,
    error_message: null,
    reference_images: [],
    split_at: FIXED_NOW,
    ...overrides,
  };
}

const GRIDS = [
  // 第 1 组：联合图已就绪、尚未切分落格，参考图多且名字长。
  grid("grid-a", GROUPS[0], { rows: 3, cols: 3, split_at: null, reference_images: REFERENCES }),
  // 第 2 组：两张联合图，第二张生成失败、错误说明很长。
  grid("grid-b1", GROUPS[1].slice(0, 9), { rows: 3, cols: 3 }),
  grid("grid-b2", GROUPS[1].slice(9), {
    rows: 2,
    cols: 3,
    status: "failed",
    grid_image_path: null,
    error_message: LONG_ERROR,
  }),
  // 第 3 组：生成中，还没有联合图。第 4 组没有联合图。
  grid("grid-c", GROUPS[2], { rows: 2, cols: 2, status: "generating", grid_image_path: null }),
];

const API: ApiOverrides = {
  // 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在替换后的数据上。
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
  "GET /api/v1/projects/demo": {
    status: 200,
    body: {
      ...project,
      project: { ...project.project, grid_storyboard: true },
      scripts: { "episode_1.json": { ...recordedScript, segments } },
    },
  },
  "GET /api/v1/projects/demo/grid-capability": { status: 200, body: { large_grid_allowed: false, max_cell_count: 9 } },
  "GET /api/v1/projects/demo/grids": { status: 200, body: GRIDS },
  ...Object.fromEntries(GRIDS.map((g) => [`GET /api/v1/projects/demo/grids/${g.id}`, { status: 200, body: g }])),
};


const groupCard = (page: Page, index: number) => page.getByRole("region", { name: new RegExp(`^第 ${index} 组`) });


async function gridReady(page: Page) {
  await page.getByRole("tab", { name: "多宫格分镜图" }).waitFor();
  await groupCard(page, 1).getByRole("img", { name: "联合图" }).waitFor();
}

/** 紧凑档 Agent 面板盖在画布右侧：先收起再点卡片右侧的控件。 */

defineRegionScenarios("多宫格分镜图", [
  {
    name: "每组一张卡片，联合图占媒体栏，页头只有「补齐联合图」",
    path: GRID_PATH,
    api: API,
    ready: gridReady,
    act: async (page) => {
      await expect(page.getByRole("tab", { name: "多宫格分镜图" })).toHaveAttribute("aria-selected", "true");
      await expect(page.getByRole("button", { name: "补齐联合图" })).toBeEnabled();
      await expect(page.getByRole("region", { name: /^第 \d 组/ })).toHaveCount(GROUP_SIZES.length);
      await expect(page.getByText("4 组 · 5 张联合图 · 33 格 · 已就绪 1 组")).toBeVisible();

      const first = groupCard(page, 1);
      await expect(first.getByText("未切分", { exact: true })).toBeVisible();
      await expect(first.getByRole("button", { name: "切分落格" })).toBeEnabled();
      // 联合图在信息栏右侧的媒体栏里，高度不超过视图区的 70%，整张可见
      const image = await box(first.getByRole("img", { name: "联合图" }));
      const card = await box(first);
      const view = await box(page.getByRole("tabpanel"));
      expect(image.x).toBeGreaterThan(card.x + 280);
      expect(image.x + image.width).toBeLessThanOrEqual(card.x + card.width);
      expect(image.height).toBeLessThanOrEqual(view.height * 0.7 + 1);
      expect(image.height).toBeGreaterThan(view.height * 0.5);

      await expect(groupCard(page, 4).getByText("这一组还没有联合图")).toBeVisible();
      await expect(groupCard(page, 4).getByRole("button", { name: "生成这一组" })).toBeEnabled();
    },
    screenshot: { name: "grid-canvas-group", target: (page) => groupCard(page, 1) },
  },
  {
    name: "切到一组里的第二张联合图，长错误说明完整显示",
    path: GRID_PATH,
    api: API,
    ready: gridReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      const second = groupCard(page, 2);
      await second.scrollIntoViewIfNeeded();
      await second.getByRole("button", { name: "第 2 张联合图" }).click();
      await expect(second.getByRole("button", { name: "第 2 张联合图" })).toHaveAttribute("aria-pressed", "true");
      await expect(second.getByText(LONG_ERROR)).toBeVisible();
      await expect(second.getByText("失败", { exact: true })).toBeVisible();
      // 没有联合图时不能切分，仍可重新生成或上传
      await expect(second.getByRole("button", { name: "切分落格" })).toBeDisabled();
      await expect(second.getByRole("button", { name: "重新生成", exact: true })).toBeEnabled();
      await expect(second.getByRole("button", { name: "上传联合图" })).toBeEnabled();
    },
    screenshot: { name: "grid-canvas-failed", target: (page) => groupCard(page, 2) },
  },
]);
