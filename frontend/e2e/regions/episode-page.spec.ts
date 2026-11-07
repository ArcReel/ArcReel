import { box, viewport, waitForEntrance, clearAgentOverlay } from "../support/region-helpers.ts";
import type { Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { FIXED_NOW, recorded } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// Episode headers: title/progress entries on one row, view tabs/current-view batch actions on another.
const EPISODE_PATH = "/app/projects/demo/episodes/1";
// Project events are unrecorded SSE; reject with nonretryable statuses, retaining recorded project data.
const API: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};


interface RecordedProject {
  project: { episodes: Record<string, unknown>[] } & Record<string, unknown>;
  scripts: Record<string, Record<string, unknown>>;
}
interface RecordedPlan {
  status: { artifacts: Record<string, Record<string, unknown>> } & Record<string, unknown>;
  steps: ({ id: string } & Record<string, unknown>)[];
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

// Stress variant: long episode names/many entries, many missing storyboard images with two generating, synthesized speech, and multicurrency costs.
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

const LONG_MESSAGE =
  "这个单元的参考图里有两位角色共用同一个名字，模型无法区分说话的人；请在分镜详情里给其中一位改名，或者删掉多余的参考图后再生成";
const BLOCKED_VIDEOS = ids(40).slice(4);

// Production-progress content stress: structural issues, generating storyboards/many stale artifacts, and forty batch-rejected video units.
// Scripts contain these forty storyboards; problems use their current episode positions.
const stressProject = (STRESS["GET /api/v1/projects/demo"] as { body: RecordedProject }).body;
const PROGRESS_STRESS: ApiOverrides = {
  ...STRESS,
  "GET /api/v1/projects/demo": {
    status: 200,
    body: {
      ...stressProject,
      scripts: {
        "episode_1.json": {
          ...project.scripts["episode_1.json"],
          segments: ids(40).map((segmentId, index) => ({
            segment_id: segmentId,
            episode: 1,
            duration_seconds: 6,
            segment_break: false,
            novel_text: `第 ${index + 1} 段原文。`,
            characters_in_segment: [],
            image_prompt: null,
            video_prompt: null,
          })),
        },
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
          storyboards: { current_ids: ids(36).slice(12), missing_ids: [], stale_ids: ids(12) },
        },
      },
      steps: plan.steps.map((step) => {
        switch (step.id) {
          case "script_structure":
            return {
              ...step,
              state: "blocked",
              problems: [
                { code: "mixed_speech", action: "replan_unit", unit_id: "E1S02", params: {} },
                { code: "empty_speaker", action: "fix_input", unit_id: "E1S07", params: {} },
                { code: "speech_admission_unrecognized", action: "fix_input", unit_id: "E1S11", params: {} },
              ],
            };
          case "storyboard":
            return {
              ...step,
              state: "active",
              artifacts: { current_ids: ids(36).slice(12), missing_ids: [], stale_ids: ids(12) },
              tasks: ["E1S13", "E1S14"].map((unitId) => ({
                unit_id: unitId,
                task_id: `task-${unitId}-0f3c9a7e5b2d4c18a6e0`,
                batch_id: null,
                task_type: "storyboard",
                status: "running",
                provider_checkpoint: { submitted: true },
              })),
            };
          case "video":
            return {
              ...step,
              state: "blocked",
              admission: {
                decision: "blocked",
                operation: "generate_videos",
                selection: "missing_only",
                units: BLOCKED_VIDEOS.map((unitId, index) => ({
                  unit_id: unitId,
                  admitted: index % 3 !== 0,
                  withheld: index % 3 !== 0,
                  problems:
                    index % 3 === 0
                      ? [{ code: "video_capability_missing_i2v", action: "configure_provider", params: {}, message: index === 0 ? LONG_MESSAGE : null }]
                      : [{ code: "generation_batch_admission_withheld", action: "none", params: {} }],
                })),
              },
            };
          default:
            return step;
        }
      }),
    },
  },
};

// Recorded episodes lack script plans; script-planning views show empty states.
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

// Two-row headers: 44px and 40px rows plus 1px bottom borders.
const HEADER_HEIGHT = 85;

const viewTabs = (page: Page) => page.getByRole("tablist", { name: "集视图" });
const header = (page: Page) => page.getByRole("main").locator("header").filter({ has: viewTabs(page) });
const viewArea = (page: Page) => page.getByRole("tabpanel");
const progress = (page: Page) => page.getByTestId("workflow-panel");
const progressPopover = (page: Page) => page.getByRole("dialog", { name: "制作进度" });



/** Wait for entry animations before measurements/axe; do not wait for ongoing spinners or other loops. */

async function pageReady(page: Page) {
  await viewTabs(page).waitFor();
  await progress(page).waitFor();
  // Task arrival triggers another plan refresh after 250ms debounce; buttons disable during refresh and fade back over 150ms afterward;
  // wait for stabilization before interactions so axe never samples colors midway through fades.
  await page.waitForLoadState("networkidle");
}

/** Compact Agent panels cover canvas right sides and header-end entries; collapse before interaction. */

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
      // Missing 36 images, 2 generating: generate only the remaining 34.
      await expect(page.getByRole("button", { name: "补齐分镜图 · 34" })).toBeEnabled();
      await expect(page.getByRole("button", { name: "视频已齐" })).toBeDisabled();
      await expect(page.getByRole("button", { name: "补齐旁白配音 · 148" })).toBeEnabled();
      // Batch actions stay outside tab lists.
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
      expect(popover.width).toBeLessThanOrEqual(560);
      expect(popover.height).toBeLessThanOrEqual(640);
      expect(popover.x).toBeGreaterThanOrEqual(0);
      expect(popover.y + popover.height).toBeLessThanOrEqual(height);
      expect(popover.x + popover.width).toBeLessThanOrEqual(width);
    },
    screenshot: { name: "episode-page-progress", target: progressPopover },
  },
  {
    name: "制作进度的提示各出现一次并标出单元，长原因与大量单元只在弹层内滚动",
    path: EPISODE_PATH,
    api: PROGRESS_STRESS,
    ready: pageReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await progress(page).click();
      const popover = progressPopover(page);
      await waitForEntrance(popover);
      const script = popover.getByTestId("workflow-row-script");
      await expect(popover.getByText("这个单元同时有角色台词和旁白，需要拆开。")).toHaveCount(1);
      await expect(script.getByRole("img", { name: "分镜 2" })).toBeVisible();
      // Generating storyboards are progress, not problems; expose no task numbers/providers.
      const boards = popover.getByTestId("workflow-row-boards");
      await expect(boards.getByText("已提交给供应商，重试可能再次计费。")).toBeVisible();
      await expect(popover.getByText(/0f3c9a7e/)).toHaveCount(0);
      await expect(popover.getByText(LONG_MESSAGE)).toHaveCount(1);
      // Popovers stay within viewports.
      const rect = await box(popover);
      expect(rect.width).toBeLessThanOrEqual(560);
      expect(rect.height).toBeLessThanOrEqual(640);
      expect(rect.y + rect.height).toBeLessThanOrEqual(viewport(page).height);
    },
    screenshot: { name: "episode-page-progress-content", target: progressPopover },
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
      // Script-planning views have no storyboard batch actions.
      await expect(page.getByRole("button", { name: /^补齐|已齐$/ })).toHaveCount(0);
    },
  },
]);
