import type { Page } from "@playwright/test";
import { clearAgentOverlay, waitForEntrance } from "../support/region-helpers.ts";
import { FIXED_NOW } from "../support/recorded.ts";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";
import { recordedDemoScript, workspaceOverride } from "../support/workspace-override.ts";

const EPISODE_PATH = "/app/projects/demo/episodes/1";
const PLAN_PATH = `${EPISODE_PATH}?view=plan`;
const SCRIPT_REVIEW = "GET /api/v1/projects/demo/episodes/1/script-review";
const TASKS = "GET /api/v1/tasks?page_size=200&project_name=demo";

const recordedScript = recordedDemoScript().script;

const BASE: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

const shotId = (n: number) => `E1S${String(n).padStart(2, "0")}`;
const LONG_TEXT =
  "夜色压低了城墙的轮廓，巡夜人提着灯从箭楼下走过，风把旗子吹得猎猎作响。沈砚停在拐角，听见护城河那边传来一声闷响，像是什么沉甸甸的东西落了水。";

function portraitSvg(hue: number) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="360" height="640" viewBox="0 0 90 160"><rect width="90" height="160" fill="hsl(${hue} 30% 22%)"/><circle cx="45" cy="62" r="20" fill="hsl(${hue} 35% 55%)"/><rect x="18" y="96" width="54" height="48" rx="6" fill="hsl(${hue} 30% 40%)"/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

const SHOT_COUNT = 40;
const segments = Array.from({ length: SHOT_COUNT }, (_, i) => ({
  segment_id: shotId(i + 1),
  episode: 1,
  duration_seconds: 4,
  segment_break: i % 8 === 0,
  novel_text: `${shotId(i + 1)}：${LONG_TEXT}`,
  characters_in_segment: [],
  image_prompt: i % 3 === 0 ? "" : `${shotId(i + 1)} 的分镜图提示词`,
  video_prompt: i % 3 === 0 ? "" : `${shotId(i + 1)} 的视频提示词`,
  ...(i === 0
    ? {
        generated_assets: {
          storyboard_image: portraitSvg(210),
          storyboard_last_image: null,
          grid_id: null,
          grid_cell_index: null,
          video_clip: null,
          video_thumbnail: null,
          video_uri: null,
          status: "storyboard_ready",
        },
      }
    : {}),
}));

const SCRIPTED: ApiOverrides = {
  ...BASE,
  ...workspaceOverride({}, { scripts: { "episode_1.json": { ...recordedScript, segments } } }),
};

function task(overrides: Record<string, unknown>) {
  return {
    task_id: "task-1",
    project_name: "demo",
    task_type: "text_script_plan",
    media_type: "text",
    resource_id: "episode-1",
    resource_type: null,
    script_file: null,
    payload: {},
    status: "failed",
    result: null,
    error_message: null,
    error_code: null,
    error_params: null,
    cancelled_by: null,
    provider_id: null,
    provider_job_id: null,
    source: "webui",
    queued_at: FIXED_NOW,
    started_at: FIXED_NOW,
    finished_at: FIXED_NOW,
    updated_at: FIXED_NOW,
    ...overrides,
  };
}

const TRUNCATED_FAILURE: ApiOverrides = {
  ...SCRIPTED,
  [TASKS]: {
    status: 200,
    body: {
      items: [
        task({
          error_message: `模型输出在第 8192 个 token 处被截断，返回的 JSON 不完整。${LONG_TEXT}`,
          error_code: "text_output_truncated",
          error_params: { provider_id: "custom-3", model: "qwen3-235b-a22b-instruct-2507", custom_model: true },
        }),
      ],
      total: 1,
      page: 1,
      page_size: 200,
    },
  },
};

const REVIEW_BASE = {
  episode: 1,
  content_mode: "narration",
  confirmed_at: null,
  blocking_issues: [],
  soft_violations: [],
  quarantine: null,
  supported_durations: null,
  duration_tiers: null,
  episode_target_duration: null,
  script_overwrite: null,
};

const planSegments = Array.from({ length: 12 }, (_, i) => ({
  segment_id: shotId(i + 1),
  novel_text: `${shotId(i + 1)}：${LONG_TEXT}`,
  duration_seconds: 6,
  segment_break: i % 4 === 0,
  characters_in_segment: i % 2 === 0 ? ["沈砚", "守备"] : ["沈砚"],
  scenes: [],
  props: [],
}));

const BLOCKING_ISSUES: ApiOverrides = {
  ...SCRIPTED,
  [SCRIPT_REVIEW]: {
    status: 200,
    body: {
      ...REVIEW_BASE,
      status: "pending_review",
      fingerprint: "fp-blocking",
      revision: "sha256-v1:fp-blocking",
      content: { segments: planSegments },
      blocking_issues: [
        { code: "episode_too_long", label: "", message: `本集总时长超出目标时长的两倍。${LONG_TEXT}`, line: null },
        { code: "missing_hook", label: "", message: "本集缺少结尾钩子。", line: null },
        ...[0, 2, 3, 5, 7, 8, 10, 11].map((index) => ({
          code: "duration_off_tier",
          label: shotId(index + 1),
          message: `${shotId(index + 1)} 的时长不在视频模型支持的档位内。`,
          line: null,
          item_index: index,
        })),
      ],
      soft_violations: [
        { code: "long_text", params: {}, item_id: "E1S02", message: "E1S02 的原文偏长，可能难以在一个分镜里讲完。", item_index: 1 },
      ],
    },
  },
};

const EMPTY_PLAN_BLOCKING: ApiOverrides = {
  ...SCRIPTED,
  [SCRIPT_REVIEW]: {
    status: 200,
    body: {
      ...REVIEW_BASE,
      status: "pending_review",
      fingerprint: "fp-empty",
      revision: "sha256-v1:fp-empty",
      content: { segments: [] },
      blocking_issues: [
        { code: "empty_script_plan", label: "", message: "脚本规划没有条目。添加至少一条后再确认。", line: null },
      ],
      soft_violations: [],
    },
  },
};

const NEW_ASSET_NAMES = ["沈砚", "守备", "说书人", "档房老吏", "渡口茶棚", "护城河", "旧档房", "铜牌", "私印", "马灯", "箭楼", "茶楼"];
const NEW_ASSET_TYPES = ["character", "character", "character", "character", "scene", "scene", "scene", "prop", "prop", "prop", "scene", "scene"];
const NEW_ASSETS: ApiOverrides = {
  ...SCRIPTED,
  [SCRIPT_REVIEW]: {
    status: 200,
    body: {
      ...REVIEW_BASE,
      status: "pending_review",
      fingerprint: "fp-new-assets",
      quarantine: null,
      content: {
        segments: planSegments,
        new_assets: NEW_ASSET_NAMES.map((name, i) => ({
          type: NEW_ASSET_TYPES[i],
          name,
          decision: i === 1 ? "merge" : i === 3 ? "derivative" : i === 9 ? "skip" : "register",
          reason: `第 ${i + 1} 段首次出场，后续多次被提及。${i % 3 === 0 ? LONG_TEXT : ""}`,
          description: i % 2 === 0 ? LONG_TEXT : "",
          aliases: [],
          target: i === 3 ? "沈砚" : "",
          asset_name: "",
        })),
      },
    },
  },
};

const AD_PROJECT: ApiOverrides = {
  ...BASE,
  ...workspaceOverride(
    {
      content_mode: "ad",
      target_duration: 30,
      brief: "面向通勤年轻人的气泡水，调性轻快，结尾引导到小程序领券。",
      products: {},
      overview: null,
    },
    { scripts: { "episode_1.json": { ...recordedScript, content_mode: "ad", segments } } },
  ),
};

const reviewBar = (page: Page) => page.locator("header").filter({ hasText: "确认阻断项" });
const newAssets = (page: Page) => page.getByRole("region", { name: "本集新增资产" });
const authoringDialog = (page: Page) => page.getByRole("dialog", { name: "编写提示词" });
const adScriptDialog = (page: Page) => page.getByRole("dialog", { name: "重新生成脚本" });
const viewer = (page: Page) => page.getByRole("dialog", { name: "分镜 1 分镜图" });
const failureNote = (page: Page) => page.getByRole("alert").filter({ hasText: "上一次 AI 规划脚本失败" });

async function reviewBlockingReady(page: Page) {
  await reviewBar(page).waitFor();
}

defineRegionScenarios("共享组件：确认阻断项与新增资产", [
  {
    name: "确认阻断项：状态条列出逐条跳转并禁用确认，整集阻断置顶，长规划只在视图内滚动",
    viewports: "both",
    a11y: true,
    path: PLAN_PATH,
    api: BLOCKING_ISSUES,
    ready: reviewBlockingReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      const bar = reviewBar(page);
      await expect(bar.getByText("10 项确认阻断项")).toBeVisible();
      await expect(bar.getByRole("button", { name: "整集 · 2" })).toBeVisible();
      await expect(bar.getByRole("button", { name: "交给 Agent 处理" })).toBeEnabled();
      await expect(bar.getByRole("button", { name: "确认并继续" })).toBeDisabled();
      await expect(page.getByText("本集缺少结尾钩子。")).toBeVisible();
      await expect(page.getByText("降级提示")).toBeVisible();
    },
  },
  {
    name: "确认阻断项：空规划无条目时仍置顶展示整集阻断并支持交给 Agent 增量修复",
    path: PLAN_PATH,
    api: EMPTY_PLAN_BLOCKING,
    ready: reviewBlockingReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      const bar = reviewBar(page);
      await expect(bar.getByText("1 项确认阻断项")).toBeVisible();
      await expect(bar.getByRole("button", { name: "整集 · 1" })).toBeVisible();
      await expect(bar.getByRole("button", { name: "确认并继续" })).toBeDisabled();
      await expect(page.getByText("脚本规划没有条目。添加至少一条后再确认。")).toBeVisible();
      await bar.getByRole("button", { name: "交给 Agent 处理" }).click();
      const composer = page.getByRole("combobox", { name: "Agent 输入" });
      await expect(composer).toHaveValue(/patch_script_plan/);
    },
  },
  {
    name: "本集新增资产：十二项长依据，展开出场位置并打开处理方式下拉",
    path: PLAN_PATH,
    api: NEW_ASSETS,
    ready: async (page) => {
      await newAssets(page).waitFor();
    },
    act: async (page) => {
      await clearAgentOverlay(page);
      const section = newAssets(page);
      const appearances = section.getByRole("button", { name: /出场 \d+ 处/ }).first();
      await appearances.click();
      await expect(appearances).toHaveAttribute("aria-expanded", "true");
      await section.getByRole("combobox", { name: "「沈砚」的处理方式" }).click();
      const listbox = page.getByRole("listbox");
      await waitForEntrance(listbox);
      await expect(listbox.getByRole("option", { name: "归到已有资产" })).toBeVisible();
    },
  },
  {
    name: "本集新增资产：按类型分组，归并与衍生的项给出目标下拉",
    path: PLAN_PATH,
    api: NEW_ASSETS,
    ready: async (page) => {
      await newAssets(page).waitFor();
    },
    act: async (page) => {
      await clearAgentOverlay(page);
      const section = newAssets(page);
      await expect(section.getByRole("region", { name: "角色", exact: true })).toBeVisible();
      await expect(section.getByRole("combobox", { name: "本体角色" })).toBeVisible();
      await section.getByRole("heading", { name: "本集新增资产" }).scrollIntoViewIfNeeded();
    },
  },
]);

defineRegionScenarios("共享组件：集页对话框与提示", [
  {
    name: "「编写提示词」：自选多条列出四十条分镜，只在列表内滚动",
    viewports: "both",
    path: EPISODE_PATH,
    api: SCRIPTED,
    ready: async (page) => {
      await page.getByRole("button", { name: "编写提示词" }).first().waitFor();
    },
    act: async (page) => {
      await clearAgentOverlay(page);
      await page.getByRole("button", { name: "编写提示词" }).first().click();
      const dialog = authoringDialog(page);
      await waitForEntrance(dialog);
      await dialog.getByRole("radio", { name: "自选多条" }).click();
      const list = dialog.getByRole("list", { name: "自选多条" });
      await expect(list.getByRole("checkbox")).toHaveCount(SHOT_COUNT);
      await expect(dialog.getByRole("button", { name: "交给 Agent" })).toBeInViewport();
    },
  },
  {
    name: "分镜图查看大图：占满视口的查看器，按 Esc 关闭",
    path: EPISODE_PATH,
    api: SCRIPTED,
    ready: async (page) => {
      await page.getByRole("button", { name: /查看「.*」的大图/ }).first().waitFor({ state: "attached" });
    },
    act: async (page) => {
      await clearAgentOverlay(page);
      const zoom = page.getByRole("button", { name: /查看「.*」的大图/ }).first();
      await zoom.hover();
      await zoom.click();
      const dialog = viewer(page);
      await waitForEntrance(dialog);
      await expect(dialog.getByRole("img")).toBeVisible();
    },
  },
  {
    name: "文本任务失败：集页顶部给出原因与去登记最大输出长度的链接",
    path: EPISODE_PATH,
    api: TRUNCATED_FAILURE,
    ready: async (page) => {
      await failureNote(page).waitFor();
    },
    act: async (page) => {
      await clearAgentOverlay(page);
      const note = failureNote(page);
      await expect(note.getByRole("link", { name: "去登记最大输出长度" })).toBeVisible();
      await expect(note.getByRole("button", { name: "关闭" })).toBeVisible();
    },
  },
  {
    name: "广告项目「重新生成脚本」对话框：附加指令只随本次提交",
    a11y: true,
    path: EPISODE_PATH,
    api: AD_PROJECT,
    ready: async (page) => {
      await page.getByRole("button", { name: "重新生成脚本" }).first().waitFor();
    },
    act: async (page) => {
      await clearAgentOverlay(page);
      await page.getByRole("button", { name: "重新生成脚本" }).first().click();
      const dialog = adScriptDialog(page);
      await waitForEntrance(dialog);
      await dialog.getByRole("textbox").fill(LONG_TEXT.repeat(3));
      await expect(dialog.getByRole("button", { name: "交给 Agent" })).toBeInViewport();
    },
  },
  {
    name: "交给 Agent 预填多行附加指令后输入框自动撑高",
    path: EPISODE_PATH,
    api: AD_PROJECT,
    ready: async (page) => {
      await page.getByRole("button", { name: "重新生成脚本" }).first().waitFor();
    },
    act: async (page) => {
      const composer = page.getByRole("combobox", { name: "Agent 输入" });
      const emptyHeight = await composer.evaluate((el) => el.clientHeight);
      await clearAgentOverlay(page);
      await page.getByRole("button", { name: "重新生成脚本" }).first().click();
      const dialog = adScriptDialog(page);
      const instruction = "每集保留一个悬念。\n优先使用已经登场的角色。\n不要重复上一集内容。";
      await dialog.getByRole("textbox").fill(instruction);
      await dialog.getByRole("button", { name: "交给 Agent" }).click();
      await expect(composer).toHaveValue(new RegExp(instruction));
      await expect.poll(() => composer.evaluate((el) => el.clientHeight)).toBeGreaterThan(emptyHeight);
      const sizing = await composer.evaluate((el) => ({
        content: el.scrollHeight, visible: el.clientHeight, overflow: getComputedStyle(el).overflowY,
      }));
      if (sizing.content > sizing.visible + 1) {
        expect(["auto", "scroll"]).toContain(sizing.overflow);
      }
    },
  },
]);
