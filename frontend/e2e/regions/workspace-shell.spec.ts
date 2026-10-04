import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Locator, Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { RECORDED_DIR, type RecordedResponse } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 项目工作区外壳：顶栏、侧栏、画布区与 Agent 面板的分栏，验证各档视口下的宽度分配、开合与调宽。
const EPISODE_PATH = "/app/projects/demo/episodes/1";
const CHARACTERS_PATH = "/app/projects/demo/characters";
// 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在录制的项目数据上。
const API: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

interface RecordedProject {
  project: { episodes: Record<string, unknown>[] };
}

// 多集且标题很长：侧栏列表与图标栏都要在自己的区域里滚动。
const recordedProject = (
  JSON.parse(readFileSync(join(RECORDED_DIR, "project-demo.json"), "utf8")) as RecordedResponse
).body as RecordedProject;
const [firstEpisode] = recordedProject.project.episodes;
const MANY_EPISODES: ApiOverrides = {
  ...API,
  "GET /api/v1/projects/demo": {
    status: 200,
    body: {
      ...recordedProject,
      project: {
        ...recordedProject.project,
        episodes: Array.from({ length: 40 }, (_, i) => ({
          ...firstEpisode,
          episode: i + 1,
          title: `第 ${i + 1} 集：夜色压低了城墙的轮廓，巡夜人提着灯从箭楼下走过，风把旗子吹得猎猎作响`,
          script_file: `scripts/episode_${i + 1}.json`,
        })),
      },
    },
  },
};

// 标准档与紧凑档的分界，与外壳的媒体查询一致。
const STANDARD_TIER_MIN_WIDTH = 1280;
const SIDEBAR_RAIL = 56;
const SIDEBAR_DEFAULT = 256;
const AGENT_DEFAULT = 420;
const AGENT_MAX = 640;
const CANVAS_MIN = 480;
const STEP = 16;

// 每行都会在面板里折成几行，最大的视口下也能撑到上限。
const NINE_LINES = Array.from(
  { length: 9 },
  (_, i) =>
    `第 ${i + 1} 行：先把这一集的节奏理一遍，开场的雨夜要压得更暗，巡夜人提灯走过箭楼的镜头放慢一些，再决定哪些镜头需要重拍、哪些只需要补一句旁白。`,
).join("\n");

const sidebar = (page: Page) => page.getByTestId("workspace-sidebar");
const canvas = (page: Page) => page.getByRole("main");
const agentPanel = (page: Page) => page.getByRole("complementary", { name: "Agent 面板" });
const agentToggle = (page: Page) => page.getByRole("button", { name: "Agent", exact: true });
const agentInput = (page: Page) => page.getByRole("combobox", { name: "Agent 输入" });
const sidebarHandle = (page: Page) => page.getByRole("separator", { name: "调整侧栏宽度（双击恢复默认）" });
const agentHandle = (page: Page) => page.getByRole("separator", { name: "调整 Agent 面板宽度（双击恢复默认）" });

/** 在手柄中线上双击。手柄只有 1px 宽，命中区由面板库按几何位置判断，元素本身可能被相邻面板盖住。 */
async function doubleClickHandle(page: Page, handle: Locator) {
  const rect = await box(handle);
  await page.mouse.dblclick(rect.x + rect.width / 2, rect.y + rect.height / 2);
}

async function box(locator: Locator) {
  const rect = await locator.boundingBox();
  if (!rect) throw new Error("元素不可见");
  return rect;
}

const width = async (locator: Locator) => (await box(locator)).width;

function viewport(page: Page) {
  const size = page.viewportSize();
  if (!size) throw new Error("没有视口尺寸");
  return size;
}

async function shellReady(page: Page) {
  await agentPanel(page).waitFor();
  await agentInput(page).waitFor();
}

async function episodeReady(page: Page) {
  await shellReady(page);
  await page.getByRole("button", { name: "添加一集" }).waitFor();
}

/** 标准档：Agent 面板挤压画布；紧凑档：画布占满侧栏右侧，Agent 面板盖在画布右侧。 */
async function expectAgentLayout(page: Page, agentWidth: number) {
  const { width: viewportWidth } = viewport(page);
  // 覆盖层盖住的宽度由面板的尺寸观察在下一帧写回画布，量之前等布局稳定
  await expect.poll(() => width(agentPanel(page))).toBeCloseTo(agentWidth, 0);
  const agent = await box(agentPanel(page));
  expect(agent.x + agent.width).toBeCloseTo(viewportWidth, 0);
  const sidebarRight = async () => {
    const rect = await box(sidebar(page));
    return rect.x + rect.width;
  };
  const canvasEdges = async () => {
    const rect = await box(canvas(page));
    return [Math.round(rect.x), Math.round(rect.x + rect.width)];
  };
  if (viewportWidth >= STANDARD_TIER_MIN_WIDTH) {
    // 画布夹在侧栏与 Agent 面板之间，两侧各有 1px 的分隔线
    await expect.poll(canvasEdges).toEqual([Math.round((await sidebarRight()) + 1), Math.round(agent.x - 1)]);
  } else {
    await expect.poll(canvasEdges).toEqual([Math.round((await sidebarRight()) + 1), viewportWidth]);
  }
}

/** 撑到上限后超出部分在内部滚动；上限是 Agent 面板高度的 40%。 */
async function expectInputCapped(page: Page) {
  const panelHeight = (await box(agentPanel(page))).height;
  const field = await agentInput(page).evaluate((el) => ({
    height: el.getBoundingClientRect().height,
    scrollHeight: el.scrollHeight,
    clientHeight: el.clientHeight,
    overflowY: getComputedStyle(el).overflowY,
  }));
  expect(field.height).toBeCloseTo(panelHeight * 0.4, 0);
  expect(field.scrollHeight).toBeGreaterThan(field.clientHeight);
  expect(["auto", "scroll"]).toContain(field.overflowY);
}

defineRegionScenarios("项目工作区外壳", [
  {
    name: "集页侧栏收为图标栏，Agent 面板默认展开",
    path: EPISODE_PATH,
    api: API,
    ready: episodeReady,
    act: async (page) => {
      expect(await width(sidebar(page))).toBeCloseTo(SIDEBAR_RAIL, 0);
      await expect(agentToggle(page)).toHaveAttribute("aria-pressed", "true");
      await expectAgentLayout(page, AGENT_DEFAULT);
      if (viewport(page).width === 1440) {
        // 1440 宽：56 + 1 + 画布 + 1 + 420
        expect(await width(canvas(page))).toBeCloseTo(962, 0);
      }
    },
    screenshot: { name: "workspace-shell-rail", target: sidebar },
  },
  {
    name: "多集长标题时侧栏列表在自己的区域里滚动",
    path: CHARACTERS_PATH,
    api: MANY_EPISODES,
    ready: async (page) => {
      await shellReady(page);
      // 侧栏展开时是列表项，收为图标栏时是带名称的链接
      const lastEpisode = { name: /第 40 集/ };
      await page.getByRole("button", lastEpisode).or(page.getByRole("link", lastEpisode)).first().waitFor({ state: "attached" });
    },
    act: async (page) => {
      const expected = viewport(page).width >= STANDARD_TIER_MIN_WIDTH ? SIDEBAR_DEFAULT : SIDEBAR_RAIL;
      expect(await width(sidebar(page))).toBeCloseTo(expected, 0);
    },
    screenshot: { name: "workspace-shell-sidebar", target: sidebar },
  },
  {
    name: "多集时集页的图标栏在自己的区域里滚动",
    path: EPISODE_PATH,
    api: MANY_EPISODES,
    ready: episodeReady,
  },
  {
    name: "紧凑档 Agent 面板覆盖画布右侧，画布不被挤窄",
    path: EPISODE_PATH,
    api: API,
    ready: episodeReady,
    act: async (page) => {
      await page.setViewportSize({ width: 1100, height: 700 });
      await expect.poll(() => width(canvas(page))).toBeCloseTo(1100 - SIDEBAR_RAIL - 1, 0);
      expect(await width(sidebar(page))).toBeCloseTo(SIDEBAR_RAIL, 0);
      await expectAgentLayout(page, AGENT_DEFAULT);
    },
  },
  {
    name: "紧凑档焦点在 Agent 面板内时 Esc 收起面板，焦点回到开关",
    path: EPISODE_PATH,
    api: API,
    ready: episodeReady,
    act: async (page) => {
      await page.setViewportSize({ width: 1100, height: 700 });
      // 等外壳切到紧凑档：画布伸到 Agent 面板底下
      await expect.poll(() => width(canvas(page))).toBeCloseTo(1100 - SIDEBAR_RAIL - 1, 0);
      await agentInput(page).focus();
      await page.keyboard.press("Escape");
      await expect(agentToggle(page)).toHaveAttribute("aria-pressed", "false");
      await expect(agentToggle(page)).toBeFocused();
      await expect.poll(() => width(canvas(page))).toBeCloseTo(1100 - SIDEBAR_RAIL - 1, 0);
    },
  },
  {
    name: "收起 Agent 面板后画布占满，再展开回到原来的宽度",
    path: EPISODE_PATH,
    api: API,
    ready: episodeReady,
    act: async (page) => {
      const { width: viewportWidth } = viewport(page);
      await agentToggle(page).click();
      await expect(agentToggle(page)).toHaveAttribute("aria-pressed", "false");
      await expect.poll(() => width(canvas(page))).toBeCloseTo(viewportWidth - SIDEBAR_RAIL - 1, 0);

      await agentToggle(page).click();
      await expect(agentToggle(page)).toHaveAttribute("aria-pressed", "true");
      await expectAgentLayout(page, AGENT_DEFAULT);
      // 最终在收起状态下探测：画布占满时也没有被裁切的内容
      await agentToggle(page).click();
    },
  },
  {
    name: "只用键盘调宽：方向键每次 16px，双击恢复默认宽度",
    path: CHARACTERS_PATH,
    api: API,
    ready: shellReady,
    act: async (page) => {
      // Agent 面板在右侧：向左变宽
      await agentHandle(page).focus();
      await page.keyboard.press("ArrowLeft");
      await expect.poll(() => width(agentPanel(page))).toBeCloseTo(AGENT_DEFAULT + STEP, 0);
      await page.keyboard.press("ArrowRight");
      await page.keyboard.press("ArrowRight");
      await expect.poll(() => width(agentPanel(page))).toBeCloseTo(AGENT_DEFAULT - STEP, 0);
      await doubleClickHandle(page, agentHandle(page));
      await expect.poll(() => width(agentPanel(page))).toBeCloseTo(AGENT_DEFAULT, 0);

      // 紧凑档侧栏收为图标栏，手柄不可用
      if (viewport(page).width < STANDARD_TIER_MIN_WIDTH) return;
      // 侧栏在左侧：向右变宽
      await sidebarHandle(page).focus();
      await page.keyboard.press("ArrowRight");
      await expect.poll(() => width(sidebar(page))).toBeCloseTo(SIDEBAR_DEFAULT + STEP, 0);
      await doubleClickHandle(page, sidebarHandle(page));
      await expect.poll(() => width(sidebar(page))).toBeCloseTo(SIDEBAR_DEFAULT, 0);
    },
  },
  {
    name: "把 Agent 面板拖到最宽时画布至少保留 480px",
    path: CHARACTERS_PATH,
    api: API,
    ready: shellReady,
    act: async (page) => {
      const handle = await box(agentHandle(page));
      await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
      await page.mouse.down();
      await page.mouse.move(0, handle.y + handle.height / 2, { steps: 8 });
      await page.mouse.up();

      const { width: viewportWidth } = viewport(page);
      if (viewportWidth >= STANDARD_TIER_MIN_WIDTH) {
        const sidebarWidth = await width(sidebar(page));
        const expected = Math.min(AGENT_MAX, viewportWidth - sidebarWidth - 2 - CANVAS_MIN);
        await expect.poll(() => width(agentPanel(page))).toBeCloseTo(expected, 0);
        expect(await width(canvas(page))).toBeGreaterThanOrEqual(CANVAS_MIN - 1);
      } else {
        await expect.poll(() => width(agentPanel(page))).toBeCloseTo(AGENT_MAX, 0);
      }
    },
  },
  {
    name: "Agent 输入框写入 9 行后撑到面板高度的 40%，超出部分在内部滚动",
    path: EPISODE_PATH,
    api: API,
    ready: episodeReady,
    act: async (page) => {
      await agentInput(page).fill(NINE_LINES);
      await expectInputCapped(page);
    },
  },
]);
