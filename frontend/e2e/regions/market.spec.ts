import type { Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 「市场」分区：铺满档，顶部 Tab（浏览 | 我的分享 | 设置）。浏览是条目网格与筛选行，
// 设置里是可排序的市场源列表、GitHub 访问代理（底部保存栏）与官方服务。

const MARKET_PATH = "/app/settings?section=market";
const SETTINGS_PATH = `${MARKET_PATH}&tab=settings`;
const SHARED_PATH = `${MARKET_PATH}&tab=shared`;

const FETCHED_AT = "2026-01-01T07:47:00.000Z";
const LONG_SOURCE = "华东二区团队共享市场 · 内部审核通过的调用端点合集（每周同步一次）";
const LONG_ENTRY = "可灵 2.1 Master 图生视频 · 华东二区备用线路（按量计费，月底结算）";
const LONG_DESCRIPTION =
  "经团队网关转发到可灵官方接口，支持首帧与尾帧、5 秒与 10 秒两档时长；提交后轮询任务状态，成功后下载视频。网关地址与密钥在新建供应商时填写，模型列表按网关实际开放的为准。";

function source(id: number, overrides: Record<string, unknown> = {}) {
  return {
    id,
    kind: id === 1 ? "official" : "custom",
    display_name: id === 1 ? "ArcReel Market" : `${LONG_SOURCE} ${id}`,
    address: id === 1 ? "ArcReel/arcreel-market" : `team-${id}/arcreel-market-mirror-with-a-long-repository-name`,
    index_url: `https://raw.githubusercontent.com/team-${id}/arcreel-market/HEAD/arcreel-market.json`,
    canonical_key: `github:team-${id}/arcreel-market@HEAD`,
    is_enabled: true,
    position: id - 1,
    status: "ok",
    last_error: null,
    fetched_at: FETCHED_AT,
    created_at: FETCHED_AT,
    updated_at: FETCHED_AT,
    entry_count: 6,
    index: { name: `源 ${id}`, description: null, homepage: `https://github.com/team-${id}/arcreel-market` },
    ...overrides,
  };
}

// 压力变体：8 个市场源，含刷新失败（错误信息很长）、索引无效与停用的源。
const SOURCES = [
  source(1),
  source(2),
  source(3, {
    status: "unreachable",
    last_error:
      "HTTP 404 Not Found: https://raw.githubusercontent.com/team-3/arcreel-market-mirror-with-a-long-repository-name/HEAD/arcreel-market.json",
  }),
  source(4, { status: "invalid_index", last_error: "endpoints/kling-master/definition.json: schema_version 必须是 1.x" }),
  source(5, { is_enabled: false, status: "never_fetched", fetched_at: null, entry_count: 0, index: null }),
  source(6),
  source(7),
  source(8),
];

const INSTALLATIONS = [
  null,
  { state: "current", modified: false },
  { state: "update_available", modified: true },
  { state: "unavailable", modified: false },
  null,
  null,
] as const;

function entry(sourceId: number, index: number) {
  const installed = INSTALLATIONS[index % INSTALLATIONS.length];
  return {
    source_id: sourceId,
    source_display_name: SOURCES[sourceId - 1].display_name,
    type: "endpoint",
    slug: `kling-master-${index}`,
    path: `endpoints/kling-master-${index}/definition.json`,
    name: `${LONG_ENTRY} ${sourceId}-${index}`,
    author: "华东二区平台组 · 团队共享",
    version: "2.1.0",
    media_type: index % 3 === 0 ? "image" : "video",
    description: LONG_DESCRIPTION,
    homepage: null,
    icon: null,
    min_app_version: index === 4 ? "9.0.0" : null,
    min_app_version_satisfied: index !== 4,
    installation: installed && {
      endpoint_id: sourceId * 10 + index,
      endpoint_key: `ce-${sourceId * 10 + index}`,
      endpoint_display_name: `${LONG_ENTRY} ${sourceId}-${index}`,
      installed_version: "2.0.0",
      ...installed,
    },
  };
}

const ENTRIES = [1, 2, 3, 4, 6].flatMap((sourceId) => Array.from({ length: 6 }, (_, index) => entry(sourceId, index)));

const OFFICIAL_SEEN = { available: true, enabled: true, notice_seen: true, instance_id: "8f14e45f-ceea-467a-9575-1c3a4d5e6f70" };

// 打开市场页时的自动刷新与官方服务聚合都访问外网，所有场景都替换。
const OFFLINE: ApiOverrides = {
  "POST /api/v1/market/refresh?stale_only=true": { status: 200, body: { sources: [] } },
  "GET /api/v1/market/entries/aggregates?type=endpoint": { status: 200, body: { items: [] } },
};

const MANY: ApiOverrides = {
  ...OFFLINE,
  "GET /api/v1/official-service": { status: 200, body: OFFICIAL_SEEN },
  "GET /api/v1/market/sources": { status: 200, body: { sources: SOURCES } },
  "GET /api/v1/market/entries?type=endpoint": { status: 200, body: { entries: ENTRIES, app_version: "0.32.0" } },
  "GET /api/v1/market/entries/aggregates?type=endpoint": {
    status: 200,
    body: {
      items: Array.from({ length: 6 }, (_, index) => ({
        source_id: 1,
        slug: `kling-master-${index}`,
        installs: 128_000 + index,
        rating_count: index * 7,
        rating_average: index > 1 ? 4.5 : null,
      })),
    },
  },
  "GET /api/v1/market/submissions": {
    status: 200,
    body: {
      submissions: Array.from({ length: 12 }, (_, index) => ({
        endpoint_id: index + 1,
        endpoint_key: `ce-${index + 1}`,
        endpoint_display_name: `${LONG_ENTRY} ${index + 1}`,
        type: "endpoint",
        slug: `kling-v2-1-master-image2video-extended-${index + 1}`,
        status: (["open", "merged", "closed"] as const)[index % 3],
        pr_url: `https://github.com/ArcReel/arcreel-market/pull/${100 + index}`,
        stale: index % 4 === 0,
      })),
    },
  },
};

const DEFINITION = {
  kind: "declarative",
  schema_version: "1.0.0",
  meta: {
    name: `${LONG_ENTRY} 1-0`,
    author: "华东二区平台组 · 团队共享",
    version: "2.1.0",
    description: LONG_DESCRIPTION,
    hints: {
      base_url: "https://gateway.example.com/v1/kling/very/long/base/path",
      suggested_models: [
        { id: "kling-v2-1-master-image2video-extended", label: "Kling 2.1 Master" },
        { id: "kling-v2-1-standard-image2video-extended", label: "Kling 2.1 Standard" },
      ],
    },
  },
  media_type: "video",
  auth: { headers: { Authorization: "Bearer {{ api_key }}", "X-Team-Gateway-Route": "east-2-backup-line" } },
  submit: {
    method: "POST",
    url: "{{ base_url }}/v1/videos/image2video/very/long/path/segment/that/keeps/going/and/going",
    body: { model: "{{ model }}", prompt: "{{ prompt }}", duration: "{{ duration }}" },
    extract: { task_id: ["$.data.task_id"] },
  },
  poll: {
    method: "GET",
    url: "{{ base_url }}/v1/videos/image2video/{{ task_id }}",
    extract: { status: ["$.data.task_status"], video_url: ["$.data.task_result.videos[0].url"] },
  },
  status_map: { succeed: "succeeded", failed: "failed" },
};

// 安装弹窗：第三方源的条目，定义长、尚未安装。
const INSTALL: ApiOverrides = {
  ...MANY,
  "GET /api/v1/market/sources/2/entries/kling-master-0": {
    status: 200,
    body: { entry: entry(2, 0), source: SOURCES[1], app_version: "0.32.0" },
  },
  "GET /api/v1/market/sources/2/entries/kling-master-0/definition": {
    status: 200,
    body: { definition: DEFINITION, entry_matches_definition: true, definition_digest: "sha256:reviewed" },
  },
  "POST /api/v1/custom-endpoints/validate": {
    status: 200,
    body: {
      errors: [],
      warnings: [],
      duplicates: [],
      hints: DEFINITION.meta.hints,
      schema_version: { file: "1.0.0", current: "1.0.0", level: "direct" },
      min_app_version: null,
      import_shape: "endpoint_definition",
      wrapped_definition: null,
    },
  },
};

const main = (page: Page) => page.getByRole("main");
const sourceList = (page: Page) => page.getByRole("region", { name: "市场源" });

async function browseReady(page: Page) {
  await page.getByRole("tab", { name: "浏览", selected: true }).waitFor();
  await page.getByText(/个调用端点/).waitFor();
}

async function settingsReady(page: Page) {
  await sourceList(page).waitFor();
  await page.getByRole("textbox", { name: "代理前缀" }).waitFor();
}

defineRegionScenarios("市场", [
  {
    name: "首次进入：官方服务说明在 Tab 之上，没有条目时说明原因",
    path: MARKET_PATH,
    api: OFFLINE,
    ready: async (page) => {
      await browseReady(page);
      await page.getByRole("region", { name: "安装量与评分来自 ArcReel 官方服务" }).waitFor();
      await page.getByText("启用的市场源里还没有条目").waitFor();
    },
    screenshot: { name: "market-first-visit", target: main },
  },
  {
    name: "条目多、名称与说明长：失败的源列在筛选行之上，网格靠左排开",
    path: MARKET_PATH,
    api: MANY,
    ready: async (page) => {
      await browseReady(page);
      await page.getByRole("status").filter({ hasText: "部分市场源刷新失败" }).waitFor();
      await expect(page.getByRole("article")).toHaveCount(ENTRIES.length);
    },
    act: async (page) => {
      // 宽窗口下内容列靠左：第一张卡片的左缘与 Tab 栏的左缘对齐
      const tabs = await page.getByRole("tablist").boundingBox();
      const card = await page.getByRole("article").first().boundingBox();
      expect(card?.x).toBe(tabs?.x);
    },
    screenshot: { name: "market-browse", target: main },
  },
  {
    name: "按来源与媒体类型筛选后没有匹配的条目",
    path: MARKET_PATH,
    api: MANY,
    ready: browseReady,
    act: async (page) => {
      await page.getByRole("group", { name: "媒体类型" }).getByRole("button", { name: "图片" }).click();
      await page.getByRole("searchbox", { name: "搜索市场条目" }).fill("没有这样的条目");
      await page.getByText("没有匹配的条目").waitFor();
    },
  },
  {
    name: "从供应商或端点带着媒体类型跳入：筛选已预设为「图片」，地址不随筛选改动",
    path: `${MARKET_PATH}&media=image`,
    api: MANY,
    ready: browseReady,
    act: async (page) => {
      const media = page.getByRole("group", { name: "媒体类型" });
      await expect(media.getByRole("button", { name: "图片" })).toHaveAttribute("aria-pressed", "true");
      const imageCount = ENTRIES.filter((item) => item.media_type === "image").length;
      await expect(page.getByRole("article")).toHaveCount(imageCount);
      await media.getByRole("button", { name: "全部" }).click();
      await expect(page.getByRole("article")).toHaveCount(ENTRIES.length);
      expect(new URL(page.url()).search).toBe("?section=market&media=image");
    },
  },
  {
    name: "我的分享：分享多、名称长，过期的状态标出原因",
    path: SHARED_PATH,
    api: MANY,
    ready: async (page) => {
      await page.getByRole("link", { name: `${LONG_ENTRY} 12` }).waitFor();
    },
    screenshot: { name: "market-shared", target: main },
  },
  {
    name: "设置：市场源多、地址与错误长",
    path: SETTINGS_PATH,
    api: MANY,
    ready: settingsReady,
    screenshot: { name: "market-settings", target: main },
  },
  {
    name: "设置：改了代理前缀后，底行的保存栏在视口内可用",
    path: SETTINGS_PATH,
    api: MANY,
    ready: settingsReady,
    act: async (page) => {
      const save = page.getByRole("button", { name: "保存", exact: true });
      await page.getByRole("textbox", { name: "代理前缀" }).fill("https://ghproxy.example.com/");
      await expect(save).toBeEnabled();
      await expect(save).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "设置：用键盘把市场源上移一位，新顺序立即保存",
    path: SETTINGS_PATH,
    api: {
      ...MANY,
      "PUT /api/v1/market/sources/order": {
        status: 200,
        body: { sources: [SOURCES[1], SOURCES[0], ...SOURCES.slice(2)].map((s, position) => ({ ...s, position })) },
      },
    },
    ready: settingsReady,
    act: async (page) => {
      const saved = page.waitForRequest(
        (request) => request.method() === "PUT" && request.url().endsWith("/api/v1/market/sources/order"),
      );
      const handle = sourceList(page).getByRole("button", { name: `调整「${LONG_SOURCE} 2」的顺序` });
      await handle.focus();
      await page.keyboard.press("Space");
      await expect(page.getByText(`已拿起「${LONG_SOURCE} 2」，位于第 2 项，共 8 项。`)).toBeAttached();
      // dnd-kit 拿起后要等测量完成才响应方向键，之前的按键会被丢掉；按到播报移动为止。
      // 目标是第 1 项，已在最前时再按上移不会继续移动，重按是安全的。
      await expect(async () => {
        await page.keyboard.press("ArrowUp");
        await expect(page.getByText(`「${LONG_SOURCE} 2」移到第 1 项，共 8 项。`)).toBeAttached({ timeout: 200 });
      }).toPass();
      await page.keyboard.press("Space");
      expect((await saved).postDataJSON()).toEqual({ ids: [2, 1, 3, 4, 5, 6, 7, 8] });
      await expect(sourceList(page).getByRole("button", { name: /^调整「.+」的顺序$/ }).first()).toHaveAccessibleName(
        `调整「${LONG_SOURCE} 2」的顺序`,
      );
    },
  },
  {
    name: "设置：打开市场源的更多操作菜单，菜单项留在视口内",
    path: SETTINGS_PATH,
    api: MANY,
    ready: settingsReady,
    act: async (page) => {
      await page.getByRole("button", { name: `「${LONG_SOURCE} 8」的更多操作` }).click();
      const menu = page.getByRole("menu");
      await expect(menu.getByRole("menuitem", { name: "删除" })).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "设置：删除市场源的确认，按钮留在视口内",
    path: SETTINGS_PATH,
    api: MANY,
    ready: settingsReady,
    act: async (page) => {
      await page.getByRole("button", { name: `「${LONG_SOURCE} 2」的更多操作` }).click();
      await page.getByRole("menuitem", { name: "删除" }).click();
      const confirm = page.getByRole("alertdialog");
      await expect(confirm.getByRole("button", { name: "删除" })).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "设置：重命名市场源的对话框",
    path: SETTINGS_PATH,
    api: MANY,
    ready: settingsReady,
    act: async (page) => {
      await page.getByRole("button", { name: `「${LONG_SOURCE} 2」的更多操作` }).click();
      await page.getByRole("menuitem", { name: "重命名" }).click();
      const dialog = page.getByRole("dialog", { name: "重命名市场源" });
      await expect(dialog.getByRole("textbox", { name: "显示名称" })).toHaveValue(`${LONG_SOURCE} 2`);
    },
  },
  {
    name: "安装弹窗：定义长、地址多，正文在弹窗里滚动，操作按钮留在视口内",
    path: MARKET_PATH,
    api: INSTALL,
    ready: browseReady,
    act: async (page) => {
      await page.getByRole("button", { name: `${LONG_ENTRY} 2-0`, exact: true }).click();
      const dialog = page.getByRole("dialog", { name: `${LONG_ENTRY} 1-0` });
      const install = dialog.getByRole("button", { name: "确认安装" });
      await expect(install).toBeEnabled();
      await expect(install).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "market-install-dialog", target: (page) => page.getByRole("dialog") },
  },
]);
