import { box, viewport, waitForEntrance, clearAgentOverlay } from "../support/region-helpers.ts";
import type { Page } from "@playwright/test";
import { defineRegionScenarios, defineViewportContracts } from "../support/scenarios.ts";
import { FIXED_NOW, RECORDED_ACCESS_TOKEN, recorded } from "../support/recorded.ts";
import { expect, test, type ApiOverrides } from "../support/test.ts";
import { recordedDemoEpisode, recordedDemoScript, recordedDemoWorkspace, workspaceOverride } from "../support/workspace-override.ts";

const EPISODE_PATH = "/app/projects/demo/episodes/1";
const API: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

interface RecordedPlan {
  status: { artifacts: Record<string, Record<string, unknown>> } & Record<string, unknown>;
  steps: ({ id: string } & Record<string, unknown>)[];
}
interface RecordedCost {
  episodes: Record<string, unknown>[];
}

const firstEpisode = recordedDemoEpisode();
const recordedScript = recordedDemoScript().script;
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

const STRESS_PROJECT_PATCH = {
  narration_delivery: "use_tts",
  episodes: [
    {
      ...firstEpisode,
      title: "第一集：夜色压低了城墙的轮廓，巡夜人提着灯从箭楼下走过，风把旗子吹得猎猎作响",
      item_count: 148,
      duration_seconds: 1234,
      videos: { total: 148, available: 112 },
    },
  ],
};

const STRESS: ApiOverrides = {
  ...API,
  ...workspaceOverride(STRESS_PROJECT_PATCH),
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
  "GET /api/v1/tasks/active?project_name=demo": { status: 200, body: { items: RUNNING_STORYBOARDS.map(runningTask), truncated: false } },
  "GET /api/v1/tasks?page_size=200&project_name=demo": {
    status: 200,
    body: { items: RUNNING_STORYBOARDS.map(runningTask), total: RUNNING_STORYBOARDS.length, page: 1, page_size: 200 },
  },
};

const LONG_MESSAGE =
  "这个单元的参考图里有两位角色共用同一个名字，模型无法区分说话的人；请在分镜详情里给其中一位改名，或者删掉多余的参考图后再生成";
const BLOCKED_VIDEOS = ids(40).slice(4);

const PROGRESS_STRESS: ApiOverrides = {
  ...STRESS,
  ...workspaceOverride(STRESS_PROJECT_PATCH, {
    scripts: {
      "episode_1.json": {
        ...recordedScript,
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
  }),
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

const NO_SCRIPT_PLAN: ApiOverrides = {
  ...API,
  "GET /api/v1/projects/demo/episodes/1/script-review": {
    status: 200,
    body: {
      episode: 1,
      content_mode: "narration",
      status: "no_script_plan",
      fingerprint: null,
      revision: null,
      confirmed_at: null,
      content: null,
      blocking_issues: [],
      soft_violations: [],
      quarantine: null,
      supported_durations: null,
      duration_tiers: null,
      episode_target_duration: null,
      script_overwrite: null,
    },
  },
};

const LONG_SOURCE = `${"夜色压低了城墙的轮廓，巡夜人提着灯从箭楼下走过。\n".repeat(120)}${"无空格长串".repeat(120)}`;
const OWN_SOURCE: ApiOverrides = {
  ...API,
  ...workspaceOverride({ episodes: [{ ...firstEpisode, source_origin: "own" }] }),
  "GET /api/v1/projects/demo/source/episode_1.txt": { status: 200, body: LONG_SOURCE },
};

const HEADER_HEIGHT = 85;

const viewTabs = (page: Page) => page.getByRole("tablist", { name: "集视图" });
const header = (page: Page) => page.getByRole("main").locator("header").filter({ has: viewTabs(page) });
const viewArea = (page: Page) => page.getByRole("tabpanel");
const progress = (page: Page) => page.getByTestId("workflow-panel");
const progressPopover = (page: Page) => page.getByRole("dialog", { name: "制作进度" });

async function pageReady(page: Page) {
  await viewTabs(page).waitFor();
  await progress(page).waitFor();
  // 任务列表到达后，防抖 250ms 还会再刷新一次计划，刷新期间按钮置灰、结束后 150ms 淡回；
  // 等它落定再操作，免得 axe 在淡入途中取色
  await page.waitForLoadState("networkidle");
}

defineRegionScenarios("集页页头", [
  {
    name: "直接打开分镜链接后选中目标，刷新已清理的地址后不再定位",
    path: `${EPISODE_PATH}?view=board&unit=E1S16&from=agent`,
    api: PROGRESS_STRESS,
    viewports: "both",
    ready: pageReady,
    act: async (page) => {
      const shots = page.getByRole("navigation", { name: "分镜列表" });
      const target = shots.locator('[data-shot-id="E1S16"]').locator('button[aria-current="true"]');
      await expect(target).toBeInViewport();
      await expect(page).toHaveURL(`${EPISODE_PATH}?view=board&from=agent`);
      await page.reload();
      await pageReady(page);
      await expect(shots.locator('[data-shot-id="E1S01"]').locator('button[aria-current="true"]')).toBeVisible();
    },
  },
  {
    name: "两行页头约 85px，批量按钮写明补几份，没有可补的置灰",
    path: EPISODE_PATH,
    api: STRESS,
    a11y: true,
    ready: async (page) => {
      await pageReady(page);
      await page.getByRole("button", { name: "补齐分镜图 · 34" }).waitFor();
    },
    act: async (page) => {
      expect((await box(header(page))).height).toBeCloseTo(HEADER_HEIGHT, 0);
      await expect(viewTabs(page).getByRole("tab", { name: "分镜" })).toHaveAttribute("aria-selected", "true");
      await expect(page.getByRole("button", { name: "补齐分镜图 · 34" })).toBeEnabled();
      await expect(page.getByRole("button", { name: "视频已齐" })).toBeDisabled();
      await expect(page.getByRole("button", { name: "补齐旁白配音 · 148" })).toBeEnabled();
      await expect(viewTabs(page).getByRole("button")).toHaveCount(0);
    },
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
    viewports: "both",
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
  },
  {
    name: "制作进度的提示各出现一次并标出单元，长原因与大量单元只在弹层内滚动",
    path: EPISODE_PATH,
    api: PROGRESS_STRESS,
    viewports: "both",
    ready: pageReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await progress(page).click();
      const popover = progressPopover(page);
      await waitForEntrance(popover);
      const script = popover.getByTestId("workflow-row-script");
      await expect(popover.getByText("这个单元同时有角色台词和旁白，需要拆开。")).toHaveCount(1);
      await expect(script.getByRole("img", { name: "分镜 2" })).toBeVisible();
      const boards = popover.getByTestId("workflow-row-boards");
      await expect(boards.getByText("已提交给供应商，重试可能再次计费。")).toBeVisible();
      await expect(popover.getByText(/0f3c9a7e/)).toHaveCount(0);
      await expect(popover.getByText(LONG_MESSAGE)).toHaveCount(1);
      const rect = await box(popover);
      expect(rect.width).toBeLessThanOrEqual(560);
      expect(rect.height).toBeLessThanOrEqual(640);
      expect(rect.y + rect.height).toBeLessThanOrEqual(viewport(page).height);
    },
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
    name: "查看原文：长原文只在弹层正文里滚动，页头高度不变",
    path: EPISODE_PATH,
    api: OWN_SOURCE,
    viewports: "both",
    a11y: true,
    ready: pageReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      expect((await box(header(page))).height).toBeCloseTo(HEADER_HEIGHT, 0);
      await page.getByRole("button", { name: "查看原文" }).click();
      const dialog = page.getByRole("dialog", { name: "本集原文" });
      await waitForEntrance(dialog);
      const body = dialog.getByRole("region", { name: "本集原文" });
      await expect(body.getByText("巡夜人提着灯", { exact: false })).toBeVisible();
      await expect(dialog.getByRole("textbox")).toHaveCount(0);
      const scroll = await body.evaluate((el) => ({ client: el.clientHeight, height: el.scrollHeight }));
      expect(scroll.height).toBeGreaterThan(scroll.client);
      const rect = await box(dialog);
      expect(rect.y + rect.height).toBeLessThanOrEqual(viewport(page).height);
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
      await expect(page.getByRole("button", { name: /^补齐|已齐$/ })).toHaveCount(0);
    },
  },
]);

defineViewportContracts("集页页头", [
  {
    name: "矮屏制作进度弹层的重新生成按钮保持对比度",
    viewport: { width: 1280, height: 600 },
    path: EPISODE_PATH,
    api: PROGRESS_STRESS,
    a11y: true,
    ready: pageReady,
    act: async (page) => {
      await progress(page).click();
      const popover = progressPopover(page);
      await waitForEntrance(popover);
      const boards = popover.getByTestId("workflow-row-boards");
      await expect(boards.getByRole("button", { name: /^重新生成 / }).first()).toBeVisible();
      const rect = await box(popover);
      expect(rect.y + rect.height).toBeLessThanOrEqual(viewport(page).height);
    },
  },
]);

test("直达单集与写后同步仅请求活动脚本，单集变更只补取该集摘要", async ({ page }) => {
  const workspace = recordedDemoWorkspace();
  const requests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/v1/projects/demo")) requests.push(url.pathname + url.search);
  });
  await page.addInitScript((token) => localStorage.setItem("arcreel_auth_token", token), RECORDED_ACCESS_TOKEN);
  const initialScript = page.waitForRequest("**/api/v1/projects/demo/scripts/episode_1.json");
  await page.route("**/api/v1/projects/demo/workspace", async (route) => {
    await initialScript;
    await route.fulfill({ json: workspace });
  });
  await page.route("**/api/v1/projects/demo/workspace?episodes=1", (route) =>
    route.fulfill({ json: { ...workspace, summary: { ...workspace.summary, episodes_summary: null } } }),
  );
  const secondScript = page.waitForRequest((request) =>
    request.url().endsWith("/scripts/episode_1.json") &&
    requests.includes("/api/v1/projects/demo/workspace?episodes=1"),
  );
  let delivered = false;
  await page.route("**/api/v1/projects/demo/events/stream", async (route) => {
    if (delivered) return route.fulfill({ status: 404, json: {} });
    delivered = true;
    await viewTabs(page).waitFor();
    const changes = [{ entity_type: "segment", entity_id: "E1S01", item_id: "E1S01", position: 1,
      episode: 1, action: "updated", important: false, label: "E1S01" }];
    const payload = { project_name: "demo", source: "webui", batch_id: "edit-1", fingerprint: "updated", changes };
    await route.fulfill({ contentType: "text/event-stream", body: `event: changes\ndata: ${JSON.stringify(payload)}\n\n` });
  });
  await page.goto(EPISODE_PATH);
  await secondScript;
  await expect(viewTabs(page)).toBeVisible();
  expect(requests.filter((path) => path.includes("/scripts/"))).toEqual(Array(2).fill("/api/v1/projects/demo/scripts/episode_1.json"));
  expect(requests.filter((path) => path.endsWith("/workspace"))).toHaveLength(1);
  expect(requests).not.toContain("/api/v1/projects/demo");
});

test("历史点段绑定按原账本在分镜画布显示正确正文", async ({ page, api }) => {
  const episode = { ...firstEpisode, script_file: "scripts/./custom.json", script_status: "none" };
  const script = {
    ...recordedScript,
    segments: [{ segment_id: "E1S99", duration_seconds: 4, novel_text: "自定义剧本正文" }],
  };
  api.override({
    ...API,
    ...workspaceOverride({ episodes: [episode] }, { scripts: { "custom.json": script } }),
    "GET /api/v1/projects/demo/grid-capability?episode=1&script_file=.%2Fcustom.json": {
      status: 200, body: recorded("project-demo-episode-grid-capability.json"),
    },
  });
  await page.addInitScript((token) => localStorage.setItem("arcreel_auth_token", token), RECORDED_ACCESS_TOKEN);
  await page.goto(EPISODE_PATH);
  await viewTabs(page).getByRole("tab", { name: "分镜", exact: true }).click();
  await expect(page.locator('[data-shot-id="E1S99"]').first()).toBeVisible();
  await expect(page.locator('[data-shot-id="E1S01"]')).toHaveCount(0);
});

test("快速切集后迟到的首集响应不能覆盖当前集", async ({ page, api }) => {
  await page.addInitScript((token) => localStorage.setItem("arcreel_auth_token", token), RECORDED_ACCESS_TOKEN);
  const second = { ...firstEpisode, episode: 2, title: "第二集目标", script_file: "scripts/episode_2.json" };
  const script = { ...recordedScript, episode: 2, segments: [{ segment_id: "E2S01", duration_seconds: 4, novel_text: "第二集正文" }] };
  api.override({
    ...API,
    ...workspaceOverride({ episodes: [firstEpisode, second] }, { scripts: { "episode_2.json": script } }),
    "GET /api/v1/projects/demo/grid-capability?episode=2&script_file=episode_2.json": {
      status: 200, body: recorded("project-demo-episode-grid-capability.json"),
    },
  });
  let release = () => {};
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/projects/demo/scripts/episode_1.json", async (route) => {
    await delayed;
    await route.fulfill({ json: recordedDemoScript() });
  });
  const firstRequest = page.waitForRequest("**/api/v1/projects/demo/scripts/episode_1.json");
  await page.goto(EPISODE_PATH);
  try {
    await firstRequest;
    const expand = page.getByRole("button", { name: "展开侧栏" });
    if (await expand.isVisible()) await expand.click();
    await page.getByText("第二集目标", { exact: true }).first().click();
    await expect(viewTabs(page)).toBeVisible();
    await expect(page).toHaveURL(/episodes\/2/);
  } finally {
    release();
  }
  await viewTabs(page).getByRole("tab", { name: "分镜", exact: true }).click();
  await expect(page.locator('[data-shot-id="E2S01"]').first()).toBeVisible();
  await expect(page.locator('[data-shot-id="E1S01"]')).toHaveCount(0);
});

test("任务由排队转为运行时持续轮询任务并保持工作流计划请求数稳定", async ({ page, api }) => {
  await page.addInitScript((token) => localStorage.setItem("arcreel_auth_token", token), RECORDED_ACCESS_TOKEN);
  api.override(API);
  let status: "queued" | "running" | "failed" = "queued";
  let runningPolls = 0;
  let planRequests = 0;
  page.on("request", (request) => {
    if (request.url().endsWith("/workflow-plan")) planRequests += 1;
  });
  const tasks = () => [{ ...runningTask("E1S01"), status, error_message: status === "failed" ? "旧任务失败" : null }];
  await page.route("**/tasks/active?project_name=demo", (route) => {
    if (status === "running") runningPolls += 1;
    return route.fulfill({ json: { items: status === "failed" ? [] : tasks(), truncated: false } });
  });
  await page.route((url) => url.pathname === "/api/v1/tasks" && url.searchParams.get("project_name") === "demo", (route) =>
    route.fulfill({ json: { items: tasks(), total: 1, page: 1, page_size: 200 } }),
  );
  await page.route("**/projects/demo/item-locations", (route) =>
    route.fulfill({ json: { items: [{ item_id: "E1S01", episode: 1, position: 7, status: "found" }] } }),
  );
  await page.goto(EPISODE_PATH);
  await pageReady(page);
  const initialPlans = planRequests;
  expect(initialPlans).toBeGreaterThan(0);
  status = "running";
  await expect.poll(() => runningPolls, { timeout: 15000 }).toBeGreaterThanOrEqual(2);
  expect(planRequests).toBe(initialPlans);
  status = "failed";
  await expect(page.getByText("分镜「第一集 · 7」生成失败：旧任务失败", { exact: true }).first()).toBeVisible({ timeout: 15000 });
});
