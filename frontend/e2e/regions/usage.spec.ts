import type { Locator, Page } from "@playwright/test";
import { FIXED_NOW } from "../support/recorded.ts";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, test, type ApiOverrides } from "../support/test.ts";

// 使用记录：设置页内的铺满档分区（筛选、KPI、趋势、构成、需要关注、记录表、详情弹窗），
// 以及工作台顶栏的使用记录弹层。录制环境没有任何调用，空态用录制数据；表格、图表与弹层
// 的压力变体用手写数据：项目标题、模型名与集名都很长，记录满一页，失败行带各类短语。

// 刻度测宽不能缓存回退字体：压力图表先收到数据，再释放项目字体，以握手稳定复现加载顺序。
test.beforeEach(async ({ page }, testInfo) => {
  if (!testInfo.title.includes("趋势、构成")) return;
  await page.route("**/*inter-latin-wght-normal*.woff2", async (route) => {
    await page.getByText("S19", { exact: true }).waitFor();
    await route.continue();
  });
});

const USAGE = "/app/settings?section=usage";

// 设置页按「最近 30 天」取数；时刻由 FIXED_NOW 推出，键随之固定。
const SUMMARY_KEY = "GET /api/v1/usage/summary?since=2025-12-02T16:00:00.000Z&tz=Asia/Shanghai";
const RECORDS_KEY = "GET /api/v1/usage/records?limit=20&since=2025-12-02T16:00:00.000Z&status=success,failed,cancelled";
const DETAIL_KEY = "GET /api/v1/usage/records/7";

const LONG_TITLE = "雨夜侦探：港口城市连环失踪案（第二季 · 导演剪辑版）";
const STATUSES = ["success", "failed", "success", "cancelled", "failed", "success"] as const;
const ERROR_CODES = ["rate_limited", "content_policy", "timeout", "download_failed", null];

function stats(calls: number) {
  return {
    calls,
    success: Math.round(calls * 0.8),
    failed: calls - Math.round(calls * 0.8) - 1,
    cancelled: 1,
    success_rate: 0.86,
    cost: { CNY: calls * 1.37, USD: calls * 0.11 },
  };
}

function record(index: number) {
  const status = STATUSES[index % STATUSES.length];
  return {
    id: index + 1,
    project_name: `rain-detective-${index % 3}`,
    project_title: index % 3 === 2 ? null : `${LONG_TITLE} ${index % 3}`,
    purpose: index % 5 === 4 ? "script_generation" : "generation_task",
    task_id: `task-${index}`,
    task_type: "video",
    media_type: (["video", "image", "audio", "text"] as const)[index % 4],
    provider: index % 2 === 0 ? "gemini-aistudio" : "custom-12",
    model: index % 2 === 0 ? "veo-3.1-fast-generate-preview" : "doubao-seedance-1-0-pro-250528-long-model-id",
    status,
    error_code: status === "failed" ? ERROR_CODES[index % ERROR_CODES.length] : null,
    error_params: null,
    error_message: status === "failed" ? "HTTP 429: quota exceeded for project 1234567890 in region asia-east1" : null,
    segment_id: index % 5 === 4 ? null : `E1S${String(index + 1).padStart(2, "0")}`,
    segment_ref:
      index % 5 === 4
        ? null
        : { episode_title: "第一集：码头上的最后一班渡轮与消失的灯塔看守人", episode_position: 1, item_id: `S${String(index + 1).padStart(2, "0")}` },
    output_path: null,
    started_at: FIXED_NOW,
    finished_at: FIXED_NOW,
    duration_ms: 41_000 + index * 1_000,
    cost_amount: status === "success" ? 3.6 : 0,
    currency: "CNY",
    input_tokens: null,
    output_tokens: null,
    usage_tokens: null,
    image_input_tokens: null,
    image_output_tokens: null,
    text_input_tokens: null,
    text_output_tokens: null,
    resolution: "1080p",
    duration_seconds: 8,
    aspect_ratio: "9:16",
    session_id: null,
  };
}

const PROJECTS = ["rain-detective-0", "rain-detective-1", "rain-detective-2", "demo#deleted-20260901T120000Z"];

const SUMMARY = {
  range: { since: "2025-12-03", until: "2026-01-01" },
  primary_currency: "CNY",
  kpi: stats(12_340),
  daily: Array.from({ length: 30 }, (_, i) => ({
    date: i < 29 ? `2025-12-${String(i + 3).padStart(2, "0")}` : "2026-01-01",
    success: 200 + ((i * 37) % 180),
    failed: (i * 13) % 40,
    cancelled: i % 7,
    cost_by_media_type: { image: 30 + i, video: 120 + ((i * 17) % 90), audio: 8, text: 3 },
  })),
  breakdown: {
    project: {
      rows: PROJECTS.map((project_name, i) => ({ project_name, ...stats(4000 - i * 900) })),
      other: { groups: 7, ...stats(320) },
    },
    provider: {
      rows: [
        { provider: "gemini-aistudio", ...stats(7000) },
        { provider: "custom-12", ...stats(5000) },
      ],
      other: null,
    },
    model: {
      rows: [{ provider: "custom-12", model: "doubao-seedance-1-0-pro-250528-long-model-id", ...stats(5000) }],
      other: null,
    },
  },
  attention: [
    {
      type: "failure_rate",
      provider: "custom-12",
      model: "doubao-seedance-1-0-pro-250528-long-model-id",
      success: 40,
      failed: 60,
      failure_rate: 0.6,
      overall_failure_rate: 0.12,
    },
    {
      type: "consecutive_failures",
      project_name: "rain-detective-0",
      media_type: "video",
      segment_id: "E1S07",
      segment_ref: { episode_title: "第一集：码头上的最后一班渡轮", episode_position: 1, item_id: "S07" },
      count: 5,
      first_failed_at: FIXED_NOW,
      last_failed_at: FIXED_NOW,
      last_error_code: "content_policy",
    },
  ],
  filter_options: {
    projects: PROJECTS,
    project_titles: { "rain-detective-0": `${LONG_TITLE} 0`, "rain-detective-1": `${LONG_TITLE} 1` },
    providers: [
      { provider: "gemini-aistudio", label: "Gemini（AI Studio）" },
      { provider: "custom-12", label: "自建火山方舟网关 · 华东二区备用线路" },
    ],
    models: [
      { provider: "gemini-aistudio", model: "veo-3.1-fast-generate-preview" },
      { provider: "custom-12", model: "doubao-seedance-1-0-pro-250528-long-model-id" },
    ],
  },
};

const DETAIL = {
  ...record(6),
  status: "failed",
  error_code: "content_policy",
  prompt: Array.from({ length: 30 }, (_, i) => `第 ${i + 1} 段：夜雨中的码头，镜头缓慢推近灯塔，雾气在探照灯里翻涌。`).join("\n"),
  inputs: { parameters: { seed: 42, negative_prompt: "blurry, low quality", camera: "dolly-in" } },
  last_provider_response: { error: { code: 400, message: "content policy violation" } },
};

const STRESS: ApiOverrides = {
  [SUMMARY_KEY]: { status: 200, body: SUMMARY },
  [RECORDS_KEY]: { status: 200, body: { items: Array.from({ length: 20 }, (_, i) => record(i)), next_cursor: "next", total: 73 } },
  [DETAIL_KEY]: { status: 200, body: DETAIL },
};

/** 等弹层的进场过渡结束再探测：淡入途中的半透明文字会被 axe 判为对比度不足。 */
async function settled(locator: Locator) {
  await locator.evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)));
}

// 工作台顶栏的使用记录弹层：本项目 KPI、进行中与最近结束的调用。
const EPISODE_PATH = "/app/projects/demo/episodes/1";
const POPOVER_STRESS: ApiOverrides = {
  // 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在录制的项目数据上。
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
  "GET /api/v1/usage/summary?project_name=demo": {
    status: 200,
    body: { ...SUMMARY, filter_options: { ...SUMMARY.filter_options, project_titles: { demo: LONG_TITLE } } },
  },
  "GET /api/v1/usage/records?limit=10&project_name=demo&status=success,failed,cancelled": {
    status: 200,
    body: { items: Array.from({ length: 10 }, (_, i) => record(i)), next_cursor: null, total: 10 },
  },
  "GET /api/v1/usage/records?project_name=demo&status=pending": {
    status: 200,
    body: {
      items: Array.from({ length: 8 }, (_, i) => ({ ...record(i), id: 100 + i, status: "pending", finished_at: null })),
      next_cursor: null,
      total: 8,
    },
  },
};

async function usageReady(page: Page) {
  await page.getByRole("heading", { name: "使用记录", level: 2 }).waitFor();
}

async function recordsReady(page: Page) {
  await usageReady(page);
  await page.getByText("S19", { exact: true }).waitFor();
}

defineRegionScenarios("使用记录", [
  {
    name: "没有任何调用时的空态",
    path: USAGE,
    ready: async (page) => {
      await usageReady(page);
      await page.getByText("还没有使用记录").waitFor();
    },
  },
  {
    name: "记录满一页且名称很长，表格滚到底后最后一行与分页可见",
    path: USAGE,
    api: STRESS,
    ready: recordsReady,
    act: async (page) => {
      // 表头不换行：每个表头单元格只有一行高。
      for (const header of await page.getByRole("columnheader").all()) {
        const box = await header.boundingBox();
        expect(box?.height ?? 0).toBeLessThan(40);
      }
      const main = page.getByRole("main");
      await main.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      await expect(page.getByRole("button", { name: "下一页" })).toBeInViewport();
    },
    // 记录区比视口高，截区域会留下滚出视口的空白；截滚到底后的主区域，即用户看到的最后几行与分页。
    screenshot: { name: "usage-records", target: (page) => page.getByRole("main") },
  },
  {
    name: "趋势、构成与需要关注在压力数据下不裁切",
    path: USAGE,
    api: STRESS,
    ready: recordsReady,
    screenshot: { name: "usage-overview", target: (page) => page.getByRole("region", { name: "趋势" }) },
  },
  {
    name: "从深链打开长提示词的详情弹窗，只有弹窗主体滚动",
    path: `${USAGE}&record=7`,
    api: STRESS,
    // 弹窗打开后页面其余部分对辅助技术不可见，只等弹窗内容。
    ready: async (page) => {
      await page.getByRole("dialog").getByText("第 30 段", { exact: false }).waitFor();
      await settled(page.getByRole("dialog"));
    },
    act: async (page) => {
      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeInViewport({ ratio: 1 });
      await expect(dialog.getByRole("button", { name: "关闭" })).toBeInViewport();
    },
    screenshot: { name: "usage-detail", target: (page) => page.getByRole("dialog") },
  },
  {
    name: "打开项目筛选下拉，长标题选项留在视口内",
    path: USAGE,
    api: STRESS,
    ready: recordsReady,
    act: async (page) => {
      await page.getByRole("combobox", { name: "项目" }).click();
      const listbox = page.getByRole("listbox");
      await settled(listbox);
      await expect(listbox).toBeInViewport();
      await expect(listbox.getByRole("option", { name: `${LONG_TITLE} 0` })).toBeVisible();
    },
  },
  {
    name: "顶栏使用记录弹层在进行中与已结束都很多时，两栏各自滚动、底部链接可见",
    path: EPISODE_PATH,
    api: POPOVER_STRESS,
    ready: async (page) => {
      await page.getByRole("button", { name: /^使用记录 · 参考费用/ }).waitFor();
    },
    act: async (page) => {
      await page.getByRole("button", { name: /^使用记录 · 参考费用/ }).click();
      const popover = page.getByRole("dialog", { name: "使用记录" });
      await popover.getByText("S09", { exact: true }).first().waitFor();
      await settled(popover);
      await expect(popover).toBeInViewport({ ratio: 1 });
      await expect(popover.getByRole("link", { name: "查看全部记录" })).toBeInViewport();
    },
    screenshot: { name: "usage-popover", target: (page) => page.getByRole("dialog", { name: "使用记录" }) },
  },
]);
