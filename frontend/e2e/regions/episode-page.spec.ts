import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Locator, Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { FIXED_NOW, RECORDED_DIR, type RecordedResponse } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 集页页头：集头与制作进度入口一行，视图 tab 与当前视图的批量动作一行。
const EPISODE_PATH = "/app/projects/demo/episodes/1";
// 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在录制的项目数据上。
const API: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

function recorded<T>(file: string): T {
  return (JSON.parse(readFileSync(join(RECORDED_DIR, file), "utf8")) as RecordedResponse).body as T;
}

interface RecordedProject {
  project: { episodes: Record<string, unknown>[] } & Record<string, unknown>;
}
interface RecordedPlan {
  status: { artifacts: Record<string, Record<string, unknown>> } & Record<string, unknown>;
}
interface RecordedCost {
  episodes: Record<string, unknown>[];
}

const project = recorded<RecordedProject>("project-demo.json");
const plan = recorded<RecordedPlan>("project-demo-workflow-plan.json");
const cost = recorded<RecordedCost>("project-demo-cost-estimate.json");

const ids = (count: number) => Array.from({ length: count }, (_, i) => `E1S${String(i + 1).padStart(2, "0")}`);
const MISSING_STORYBOARDS = ids(36);
const RUNNING_STORYBOARDS = MISSING_STORYBOARDS.slice(0, 2);

function runningTask(resourceId: string) {
  return {
    task_id: `task-${resourceId}`,
    project_name: "demo",
    task_type: "storyboard",
    media_type: "image",
    resource_id: resourceId,
    resource_type: null,
    script_file: "episode_1.json",
    payload: {},
    status: "running",
    result: null,
    error_message: null,
    cancelled_by: null,
    provider_id: null,
    provider_job_id: null,
    source: "webui",
    queued_at: FIXED_NOW,
    started_at: FIXED_NOW,
    finished_at: null,
    updated_at: FIXED_NOW,
  };
}

// 压力变体：长集名、条目多、计划里缺很多分镜图且有两张正在生成、配音走配音合成、费用多币种。
const STRESS: ApiOverrides = {
  ...API,
  "GET /api/v1/projects/demo": {
    status: 200,
    body: {
      ...project,
      project: {
        ...project.project,
        narration_delivery: "use_tts",
        episodes: [
          {
            ...project.project.episodes[0],
            title: "第一集：夜色压低了城墙的轮廓，巡夜人提着灯从箭楼下走过，风把旗子吹得猎猎作响",
            item_count: 148,
            duration_seconds: 1234,
            videos: { total: 148, available: 112, stale: 3 },
          },
        ],
      },
    },
  },
  "POST /api/v1/projects/demo/workflow-plan": {
    status: 200,
    body: {
      ...plan,
      status: {
        ...plan.status,
        artifacts: {
          ...plan.status.artifacts,
          storyboards: { current_ids: [], missing_ids: MISSING_STORYBOARDS, stale_ids: [] },
          videos: { current_ids: ids(148), missing_ids: [], stale_ids: [] },
          audio: { current_ids: [], missing_ids: ids(148), stale_ids: [] },
        },
      },
    },
  },
  "GET /api/v1/projects/demo/cost-estimate": {
    status: 200,
    body: {
      ...cost,
      episodes: [
        {
          ...cost.episodes[0],
          totals: {
            estimate: { image: { USD: 12.5, CNY: 86 }, video: { USD: 340.75, CNY: 1280 } },
            actual: { video: { USD: 120.4 }, unassigned: { CNY: 32 } },
          },
        },
      ],
    },
  },
  "GET /api/v1/tasks?page_size=200&project_name=demo": {
    status: 200,
    body: { items: RUNNING_STORYBOARDS.map(runningTask), total: RUNNING_STORYBOARDS.length, page: 1, page_size: 200 },
  },
};

// 录制的集还没有脚本规划，脚本规划视图显示空态。
const NO_SCRIPT_PLAN: ApiOverrides = {
  ...API,
  "GET /api/v1/projects/demo/episodes/1/script-review": {
    status: 200,
    body: {
      episode: 1,
      content_mode: "narration",
      status: "no_script_plan",
      fingerprint: null,
      confirmed_at: null,
      content: null,
      quarantine: null,
      supported_durations: null,
      duration_tiers: null,
      episode_target_duration: null,
      script_overwrite: null,
    },
  },
};

// 两行页头：44px 与 40px 两行加 1px 底边。
const HEADER_HEIGHT = 85;
const COMPACT_TIER_MAX_WIDTH = 1279;

const viewTabs = (page: Page) => page.getByRole("tablist", { name: "集视图" });
const header = (page: Page) => page.getByRole("main").locator("header").filter({ has: viewTabs(page) });
const viewArea = (page: Page) => page.getByRole("tabpanel");
const progress = (page: Page) => page.getByTestId("workflow-panel");
const progressPopover = (page: Page) => page.getByRole("dialog", { name: "制作进度" });
const agentToggle = (page: Page) => page.getByRole("button", { name: "Agent", exact: true });

async function box(locator: Locator) {
  const rect = await locator.boundingBox();
  if (!rect) throw new Error("元素不可见");
  return rect;
}

function viewport(page: Page) {
  const size = page.viewportSize();
  if (!size) throw new Error("没有视口尺寸");
  return size;
}

/** 弹层带入场动画，量尺寸、跑 axe 之前等它停下；进行中的转圈等循环动画不等。 */
async function waitForEntrance(target: Locator) {
  await target.evaluate((el) =>
    Promise.allSettled(
      el
        .getAnimations({ subtree: true })
        .filter((animation) => animation.effect?.getComputedTiming().endTime !== Infinity)
        .map((animation) => animation.finished),
    ),
  );
}

async function pageReady(page: Page) {
  await viewTabs(page).waitFor();
  await progress(page).waitFor();
}

/** 紧凑档 Agent 面板盖在画布右侧，页头行尾的入口在它底下：先收起再操作。 */
async function clearAgentOverlay(page: Page) {
  if (viewport(page).width > COMPACT_TIER_MAX_WIDTH) return;
  await agentToggle(page).click();
  await expect(agentToggle(page)).toHaveAttribute("aria-pressed", "false");
}

defineRegionScenarios("集页页头", [
  {
    name: "两行页头约 85px，批量按钮写明补几份，没有可补的置灰",
    path: EPISODE_PATH,
    api: STRESS,
    ready: async (page) => {
      await pageReady(page);
      await page.getByRole("button", { name: "补齐分镜图 · 34" }).waitFor();
    },
    act: async (page) => {
      expect((await box(header(page))).height).toBeCloseTo(HEADER_HEIGHT, 0);
      await expect(viewTabs(page).getByRole("tab", { name: "分镜" })).toHaveAttribute("aria-selected", "true");
      // 缺 36 张、2 张正在生成：只补剩下的 34 张
      await expect(page.getByRole("button", { name: "补齐分镜图 · 34" })).toBeEnabled();
      await expect(page.getByRole("button", { name: "视频已齐" })).toBeDisabled();
      await expect(page.getByRole("button", { name: "补齐旁白配音 · 148" })).toBeEnabled();
      // 批量动作不在 tab 列表里
      await expect(viewTabs(page).getByRole("button")).toHaveCount(0);
    },
    screenshot: { name: "episode-page-header", target: header },
  },
  {
    name: "录制的集：没有可补的产物时三个批量按钮都置灰",
    path: EPISODE_PATH,
    api: API,
    ready: pageReady,
    act: async (page) => {
      await expect(page.getByRole("button", { name: "分镜图已齐" })).toBeDisabled();
      await expect(page.getByRole("button", { name: "视频已齐" })).toBeDisabled();
    },
  },
  {
    name: "打开制作进度不改变视图区高度，弹层留在视口内",
    path: EPISODE_PATH,
    api: STRESS,
    ready: pageReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      const before = await box(viewArea(page));
      await progress(page).click();
      await waitForEntrance(progressPopover(page));
      const popover = await box(progressPopover(page));
      const after = await box(viewArea(page));
      expect(after.y).toBeCloseTo(before.y, 0);
      expect(after.height).toBeCloseTo(before.height, 0);
      const { width, height } = viewport(page);
      expect(popover.x).toBeGreaterThanOrEqual(0);
      expect(popover.y + popover.height).toBeLessThanOrEqual(height);
      expect(popover.x + popover.width).toBeLessThanOrEqual(width);
    },
    screenshot: { name: "episode-page-progress", target: progressPopover },
  },
  {
    name: "集头「⋯」菜单展开删除入口",
    path: EPISODE_PATH,
    api: STRESS,
    ready: pageReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await page.getByRole("button", { name: "这一集的更多操作" }).click();
      await expect(page.getByRole("menuitem", { name: "删除这一集" })).toBeVisible();
      await waitForEntrance(page.getByRole("menu"));
    },
  },
  {
    name: "地址带 view=plan 时直接打开脚本规划视图",
    path: `${EPISODE_PATH}?view=plan`,
    api: NO_SCRIPT_PLAN,
    ready: async (page) => {
      await pageReady(page);
      await page.getByText("暂无脚本规划结果").waitFor();
    },
    act: async (page) => {
      await expect(viewTabs(page).getByRole("tab", { name: "脚本规划" })).toHaveAttribute("aria-selected", "true");
      await expect(page.getByRole("tabpanel", { name: "脚本规划" })).toBeVisible();
      // 脚本规划视图没有分镜的批量动作
      await expect(page.getByRole("button", { name: /^补齐|已齐$/ })).toHaveCount(0);
    },
  },
]);
