import type { Page } from "@playwright/test";
import { clearAgentOverlay, waitForEntrance } from "../support/region-helpers.ts";
import { FIXED_NOW, recorded } from "../support/recorded.ts";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 各区域共用的组件：待修复草稿的状态条与两个确认弹层、内容确认页的「本集新增资产」、「编写提示词」与
// 广告「AI 生成脚本」对话框、分镜图的查看大图，以及集页顶部的文本任务失败提示。
const EPISODE_PATH = "/app/projects/demo/episodes/1";
const PLAN_PATH = `${EPISODE_PATH}?view=plan`;
const PROJECT = "GET /api/v1/projects/demo";
const SCRIPT_REVIEW = "GET /api/v1/projects/demo/episodes/1/script-review";
const TASKS = "GET /api/v1/tasks?page_size=200&project_name=demo";

interface RecordedProject {
  project: Record<string, unknown> & { episodes: Record<string, unknown>[] };
  scripts: Record<string, Record<string, unknown>>;
}

const recordedProject = recorded<RecordedProject>("project-demo.json");
const recordedScript = recordedProject.scripts["episode_1.json"];

// 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在替换后的数据上。
const BASE: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

const shotId = (n: number) => `E1S${String(n).padStart(2, "0")}`;
const LONG_TEXT =
  "夜色压低了城墙的轮廓，巡夜人提着灯从箭楼下走过，风把旗子吹得猎猎作响。沈砚停在拐角，听见护城河那边传来一声闷响，像是什么沉甸甸的东西落了水。";

// 分镜图用内联 SVG：getFileUrl 原样使用 data: 地址，不发请求，截图稳定。
function portraitSvg(hue: number) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="360" height="640" viewBox="0 0 90 160"><rect width="90" height="160" fill="hsl(${hue} 30% 22%)"/><circle cx="45" cy="62" r="20" fill="hsl(${hue} 35% 55%)"/><rect x="18" y="96" width="54" height="48" rx="6" fill="hsl(${hue} 30% 40%)"/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

// 四十条分镜的正式脚本，第一条已有分镜图：「编写提示词」的自选列表与查看大图都用它。
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
  [PROJECT]: {
    status: 200,
    body: {
      ...recordedProject,
      scripts: { "episode_1.json": { ...recordedScript, segments } },
    },
  },
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

// 上一次 AI 规划脚本因输出被截断失败，用的是自定义供应商的模型：提示给出去登记最大输出长度的链接。
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

// 待修复草稿压力：十二条分镜里多处违约（含整集层面的两条）与降级提示。
const INVALID_DRAFT: ApiOverrides = {
  ...SCRIPTED,
  [SCRIPT_REVIEW]: {
    status: 200,
    body: {
      ...REVIEW_BASE,
      status: "pending_review",
      fingerprint: null,
      content: null,
      quarantine: {
        doc_type: "narration_script_plan",
        revision: "rev-1",
        editable_by: "user",
        content: { segments: planSegments },
        violations: [
          { code: "episode_too_long", label: "", message: `本集总时长超出目标时长的两倍。${LONG_TEXT}`, line: null },
          { code: "missing_hook", label: "", message: "本集缺少结尾钩子。", line: null },
          ...[0, 2, 3, 5, 7, 8, 10, 11].map((index) => ({
            code: "duration_out_of_range",
            label: shotId(index + 1),
            message: `${shotId(index + 1)} 的时长不在视频模型支持的档位内。`,
            line: null,
            item_index: index,
          })),
        ],
        soft_violations: [{ code: "long_text", message: `E1S02 的原文偏长，可能难以在一个分镜里讲完。`, item_index: 1 }],
        formal_exists: true,
      },
    },
  },
};

// 内容确认页的「本集新增资产」压力：十二项，长依据、长描述与多处出场。
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

// 广告项目，第一集已有正式脚本：页头的「重新生成脚本」打开整份重做的对话框。
const AD_PROJECT: ApiOverrides = {
  ...BASE,
  [PROJECT]: {
    status: 200,
    body: {
      ...recordedProject,
      project: {
        ...recordedProject.project,
        content_mode: "ad",
        target_duration: 30,
        brief: "面向通勤年轻人的气泡水，调性轻快，结尾引导到小程序领券。",
        products: {},
        overview: null,
      },
      scripts: { "episode_1.json": { ...recordedScript, content_mode: "ad", segments } },
    },
  },
};

const draftBar = (page: Page) => page.locator("header").filter({ hasText: "待修复草稿" });
const repairDialog = (page: Page) => page.getByRole("dialog", { name: "AI 修复草稿" });
const discardDialog = (page: Page) => page.getByRole("alertdialog", { name: "丢弃这份草稿？" });
const newAssets = (page: Page) => page.getByRole("region", { name: "本集新增资产" });
const authoringDialog = (page: Page) => page.getByRole("dialog", { name: "编写提示词" });
const adScriptDialog = (page: Page) => page.getByRole("dialog", { name: "重新生成脚本" });
const viewer = (page: Page) => page.getByRole("dialog", { name: /E1S01|S01/ });
const failureNote = (page: Page) => page.getByRole("alert").filter({ hasText: "上一次 AI 规划脚本失败" });

async function draftReady(page: Page) {
  await draftBar(page).waitFor();
}

defineRegionScenarios("共享组件：待修复草稿与新增资产", [
  {
    name: "待修复草稿：状态条列出逐条跳转，整集违约置顶，长草稿只在视图内滚动",
    path: PLAN_PATH,
    api: INVALID_DRAFT,
    ready: draftReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      const bar = draftBar(page);
      await expect(bar.getByText("10 处违约")).toBeVisible();
      await expect(bar.getByRole("button", { name: "整集 · 2" })).toBeVisible();
      await expect(bar.getByRole("button", { name: "交给 Agent" })).toBeEnabled();
      await expect(page.getByText("本集缺少结尾钩子。")).toBeVisible();
    },
    screenshot: { name: "shared-invalid-draft-bar", target: draftBar },
  },
  {
    name: "待修复草稿：「AI 修复」对话框说明修复范围并接受附加指令",
    path: PLAN_PATH,
    api: INVALID_DRAFT,
    ready: draftReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await draftBar(page).getByRole("button", { name: "AI 修复" }).click();
      const dialog = repairDialog(page);
      await waitForEntrance(dialog);
      await dialog.getByRole("textbox").fill(LONG_TEXT.repeat(4));
      await expect(dialog.getByRole("button", { name: "取消" })).toBeVisible();
    },
    screenshot: { name: "shared-draft-repair-dialog", target: repairDialog },
  },
  {
    name: "待修复草稿：丢弃前确认，写明丢弃后回到哪份内容",
    path: PLAN_PATH,
    api: INVALID_DRAFT,
    ready: draftReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await draftBar(page).getByRole("button", { name: "丢弃草稿" }).click();
      const dialog = discardDialog(page);
      await waitForEntrance(dialog);
      await expect(dialog.getByRole("button", { name: "取消" })).toBeFocused();
      await expect(dialog.getByRole("button", { name: "丢弃草稿" })).toBeVisible();
    },
    screenshot: { name: "shared-draft-discard", target: discardDialog },
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
    screenshot: { name: "shared-new-assets", target: (page) => newAssets(page).getByRole("region", { name: "角色", exact: true }) },
  },
]);

defineRegionScenarios("共享组件：集页对话框与提示", [
  {
    name: "「编写提示词」：自选多条列出四十条分镜，只在列表内滚动",
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
    screenshot: { name: "shared-prompt-authoring", target: authoringDialog },
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
    screenshot: { name: "shared-image-viewer", target: viewer },
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
    screenshot: { name: "shared-text-task-failure", target: failureNote },
  },
  {
    name: "广告项目「重新生成脚本」对话框：附加指令只随本次提交",
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
    screenshot: { name: "shared-ad-script-dialog", target: adScriptDialog },
  },
]);
