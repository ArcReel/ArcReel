import type { Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 全局设置的「外部 Agent 接入」与「访问令牌」：步骤页、创建令牌对话框（含一次性展示的完整令牌）、
// 多条长名称令牌的列表与吊销确认。

const EXTERNAL_AGENT = "/app/settings?section=external-agent";
const ACCESS_TOKENS = "/app/settings?section=access-tokens";

// 压力变体：令牌多、名称长，永久、已过期、未过期与从未使用混在一起（固定时间是 2026-01-01）。
const MANY_TOKENS: ApiOverrides = {
  "GET /api/v1/api-keys": {
    status: 200,
    body: Array.from({ length: 18 }, (_, i) => ({
      id: i + 1,
      name: `华东二区渲染集群 · 夜间批量出片流水线 ${i + 1}（运维值班专用，勿删）`,
      key_prefix: `arc-${String(i + 1).padStart(4, "0")}`,
      created_at: "2025-11-02T03:00:00Z",
      expires_at: i % 3 === 0 ? null : i % 3 === 1 ? "2025-12-01T00:00:00Z" : "2026-06-30T00:00:00Z",
      last_used_at: i % 2 === 0 ? "2025-12-30T12:00:00Z" : null,
    })),
  },
};

const FEW_TOKENS: ApiOverrides = {
  "GET /api/v1/api-keys": {
    status: 200,
    body: [
      { id: 1, name: "我的外部 Agent", key_prefix: "arc-7d1e", created_at: "2025-12-20T03:00:00Z", expires_at: "2026-01-19T03:00:00Z", last_used_at: "2025-12-31T09:00:00Z" },
      { id: 2, name: "CI 流水线", key_prefix: "arc-4a6b", created_at: "2025-11-02T03:00:00Z", expires_at: null, last_used_at: null },
      { id: 3, name: "旧笔记本", key_prefix: "arc-0c2f", created_at: "2025-10-01T03:00:00Z", expires_at: "2025-12-01T00:00:00Z", last_used_at: "2025-11-28T12:00:00Z" },
    ],
  },
};

const CREATED_TOKEN: ApiOverrides = {
  "POST /api/v1/api-keys": {
    status: 200,
    body: {
      id: 99,
      name: "我的外部 Agent",
      key: `arc-${"9f3b2c7d1e4a6b8c".repeat(4)}`,
      key_prefix: "arc-9f3b",
      created_at: "2026-01-01T08:00:00Z",
      expires_at: "2026-01-31T08:00:00Z",
    },
  },
};

// 吊销失败的原因很长：说明在只读的对话框正文里折行，超出可用高度后由正文滚动。
const REVOKE_FAILED: ApiOverrides = {
  ...MANY_TOKENS,
  "DELETE /api/v1/api-keys/2": {
    status: 503,
    body: { detail: `令牌存储暂时不可用：${"上游数据库连接池已耗尽，吊销请求进入重试队列后仍未完成。".repeat(60)}` },
  },
};

async function settingsReady(page: Page) {
  await page.getByRole("navigation", { name: "设置" }).getByRole("link", { name: "关于" }).waitFor();
}

async function externalAgentReady(page: Page) {
  await settingsReady(page);
  await page.getByRole("heading", { name: "外部 Agent 接入", level: 2 }).waitFor();
}

async function openCreateDialog(page: Page) {
  await page.getByRole("button", { name: "创建访问令牌" }).click();
  await page.getByRole("dialog", { name: "创建访问令牌" }).waitFor();
}

defineRegionScenarios("外部 Agent 接入与访问令牌", [
  {
    name: "外部 Agent 接入按三步展示，滚到底时给 AI Agent 的提示词与复制按钮可达",
    path: EXTERNAL_AGENT,
    ready: externalAgentReady,
    act: async (page) => {
      const main = page.getByRole("main");
      await main.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      await expect(page.getByRole("button", { name: "复制提示词" })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "external-agent", target: (page) => page.getByRole("main"), showsOrigin: true },
  },
  {
    name: "第三步打开创建访问令牌对话框，表单与按钮留在视口内",
    path: EXTERNAL_AGENT,
    ready: externalAgentReady,
    act: async (page) => {
      await openCreateDialog(page);
      const dialog = page.getByRole("dialog", { name: "创建访问令牌" });
      await expect(dialog).toBeInViewport({ ratio: 1 });
      // 打开时焦点落在名称输入框，回车即可提交
      await expect(dialog.getByRole("textbox", { name: "名称" })).toBeFocused();
    },
    screenshot: { name: "access-token-create", target: (page) => page.getByRole("dialog") },
  },
  {
    name: "创建成功后对话框一次性展示完整令牌，长令牌折行不撑出视口",
    path: EXTERNAL_AGENT,
    api: CREATED_TOKEN,
    ready: externalAgentReady,
    act: async (page) => {
      await openCreateDialog(page);
      const dialog = page.getByRole("dialog", { name: "创建访问令牌" });
      await dialog.getByRole("textbox", { name: "名称" }).fill("我的外部 Agent");
      await dialog.getByRole("textbox", { name: "名称" }).press("Enter");
      const created = page.getByRole("dialog", { name: "访问令牌已创建" });
      await expect(created.getByRole("button", { name: "复制访问令牌" })).toBeVisible();
      await expect(created).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "access-token-created", target: (page) => page.getByRole("dialog") },
  },
  {
    name: "访问令牌多且名称长时，列表在外壳主体里滚动到底，吊销按钮可达",
    path: ACCESS_TOKENS,
    api: MANY_TOKENS,
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("table", { name: "访问令牌列表" }).waitFor();
    },
    act: async (page) => {
      const main = page.getByRole("main");
      await main.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      await expect(page.getByRole("button", { name: /吊销「.*流水线 18（/ })).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "访问令牌列表区分永久、已过期与从未使用",
    path: ACCESS_TOKENS,
    api: FEW_TOKENS,
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("table", { name: "访问令牌列表" }).waitFor();
    },
    // 只截令牌表：分区顶部的配置问题横幅不属于本区域，不进本区域的基线
    screenshot: { name: "access-tokens", target: (page) => page.getByRole("table", { name: "访问令牌列表" }) },
  },
  {
    name: "吊销确认对话框打开时，焦点在取消上且按钮留在视口内",
    path: ACCESS_TOKENS,
    api: MANY_TOKENS,
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("table", { name: "访问令牌列表" }).waitFor();
    },
    act: async (page) => {
      await page.getByRole("button", { name: /吊销「.*流水线 1（/ }).click();
      const confirm = page.getByRole("alertdialog");
      await expect(confirm).toBeInViewport({ ratio: 1 });
      await expect(confirm.getByRole("button", { name: "取消" })).toBeFocused();
    },
  },
  {
    name: "吊销失败的原因很长时，对话框正文可用键盘聚焦并滚动，按钮留在视口内",
    path: ACCESS_TOKENS,
    api: REVOKE_FAILED,
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("table", { name: "访问令牌列表" }).waitFor();
    },
    act: async (page) => {
      await page.getByRole("button", { name: /吊销「.*流水线 2（/ }).click();
      const confirm = page.getByRole("alertdialog");
      await confirm.getByRole("button", { name: "吊销", exact: true }).click();
      await expect(confirm.getByRole("alert")).toContainText("吊销失败");
      const body = confirm.getByRole("region", { name: /吊销访问令牌/ });
      await body.focus();
      await expect(body).toBeFocused();
      await page.keyboard.press("End");
      await expect.poll(() => body.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
      await expect(confirm.getByRole("button", { name: "取消" })).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "还没有访问令牌时显示空状态",
    path: ACCESS_TOKENS,
    ready: async (page) => {
      await settingsReady(page);
      await page.getByText("还没有访问令牌").waitFor();
    },
  },
]);
