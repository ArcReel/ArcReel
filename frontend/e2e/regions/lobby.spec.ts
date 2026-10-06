import type { Page } from "@playwright/test";
import { FIXED_NOW } from "../support/recorded.ts";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 项目大厅：铺满档，没有侧栏。顶栏是品牌、搜索、资产库、外部 Agent 接入、「新建项目」分体按钮与设置；
// 主体是问候区、吸顶的筛选工具栏与海报卡网格。

const LOBBY_PATH = "/app/projects";
const DAY_MS = 24 * 60 * 60 * 1000;

const LONG_TITLE = "雾港来信：一个关于灯塔守夜人、失踪的邮差与三十年后才寄到的那封信的长篇悬疑故事";
const LONG_REASON =
  "项目结构升级到第 7 版时失败：scripts/episode_12.json 的 segments[48].clues_in_segment 引用了已删除的线索「旧邮戳」，补录分镜图时找不到对应的资产文件 assets/props/old-postmark-v3.png";

interface ProjectOptions {
  episodes?: { total: number; completed?: number; inProduction?: number };
  needsRepair?: boolean;
  daysAgo?: number;
}

function project(index: number, title: string, options: ProjectOptions = {}) {
  const { total = 0, completed = 0, inProduction = 0 } = options.episodes ?? { total: 0 };
  return {
    name: `project-${index}`,
    title,
    style: "",
    style_template_id: null,
    style_image: null,
    thumbnail: null,
    status: {
      schema_version: 2,
      needs_repair: options.needsRepair ?? false,
      repair_reason: options.needsRepair ? LONG_REASON : null,
      assets: {
        character: { total: 0, available: 0, stale: 0 },
        scene: { total: 0, available: 0, stale: 0 },
        prop: { total: 0, available: 0, stale: 0 },
        product: { total: 0, available: 0, stale: 0 },
      },
      episodes_summary: { total, scripted: total, in_production: inProduction, completed },
      source_remaining: false,
    },
    last_activity_at: new Date(Date.parse(FIXED_NOW) - (options.daysAgo ?? index) * DAY_MS).toISOString(),
  };
}

// 压力变体：24 个项目，标题长、待修复原因长、集数多，覆盖三种进度。
const PROJECTS = Array.from({ length: 24 }, (_, index) => {
  const variant = index % 4;
  if (variant === 0) return project(index, `${LONG_TITLE} 第 ${index + 1} 部`, { episodes: { total: 40, completed: 12, inProduction: 6 } });
  if (variant === 1) return project(index, `短片 ${index + 1}`, { episodes: { total: 6, completed: 6 } });
  if (variant === 2) return project(index, `${LONG_TITLE} ${index + 1}`, { episodes: { total: 3 }, needsRepair: true });
  return project(index, `新项目 ${index + 1}`);
});

const MANY: ApiOverrides = { "GET /api/v1/projects": { status: 200, body: { projects: PROJECTS } } };
const EMPTY: ApiOverrides = { "GET /api/v1/projects": { status: 200, body: { projects: [] } } };

const main = (page: Page) => page.getByRole("main");
const projectList = (page: Page) => page.getByRole("list", { name: "项目" });
const firstCardActions = (page: Page) => page.getByRole("button", { name: `「${LONG_TITLE} 第 1 部」的更多操作` });

async function lobbyReady(page: Page) {
  await projectList(page).getByRole("listitem").first().waitFor();
}

async function manyReady(page: Page) {
  await expect(projectList(page).getByRole("listitem")).toHaveCount(PROJECTS.length);
}

defineRegionScenarios("项目大厅", [
  {
    name: "打开大厅：问候区、筛选工具栏与项目卡",
    path: LOBBY_PATH,
    ready: async (page) => {
      await lobbyReady(page);
      await page.getByRole("heading", { level: 1 }).waitFor();
    },
    screenshot: { name: "lobby-default" },
  },
  {
    name: "项目多、标题与待修复原因长：首行海报、标题与进度行在首屏内",
    path: LOBBY_PATH,
    api: MANY,
    ready: manyReady,
    act: async (page) => {
      const first = projectList(page).getByRole("listitem").first();
      await expect(first.getByRole("heading", { level: 3 })).toBeInViewport({ ratio: 1 });
      await expect(first.getByText("制作中", { exact: true })).toBeInViewport({ ratio: 1 });

      // 卡片最小宽 280px，2560 宽的窗口排 8 列
      if (page.viewportSize()?.width === 2560) {
        const lefts = await projectList(page)
          .getByRole("listitem")
          .evaluateAll((items) => new Set(items.map((item) => Math.round(item.getBoundingClientRect().left))).size);
        expect(lefts).toBe(8);
      }
    },
    screenshot: { name: "lobby-many", target: main },
  },
  {
    name: "滚到网格底部时，筛选工具栏吸顶留在视口内",
    path: LOBBY_PATH,
    api: MANY,
    ready: manyReady,
    act: async (page) => {
      await projectList(page).getByRole("listitem").last().scrollIntoViewIfNeeded();
      await expect(page.getByRole("group", { name: "按进度筛选" })).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "没有项目：空状态给出新建与导入两个入口",
    path: LOBBY_PATH,
    api: EMPTY,
    ready: async (page) => {
      await page.getByText("还没有项目").waitFor();
    },
    screenshot: { name: "lobby-empty", target: main },
  },
  {
    name: "搜索没有匹配的项目",
    path: LOBBY_PATH,
    api: MANY,
    ready: manyReady,
    act: async (page) => {
      await page.getByRole("searchbox", { name: "搜索项目" }).fill("没有这样的项目");
      await page.getByText("没有匹配的项目").waitFor();
    },
  },
  {
    name: "打开「新建项目」的下拉菜单",
    path: LOBBY_PATH,
    api: MANY,
    ready: manyReady,
    act: async (page) => {
      await page.getByRole("button", { name: "更多新建方式" }).click();
      await expect(page.getByRole("menuitem", { name: "导入 ZIP…" })).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "打开项目卡的更多操作菜单",
    path: LOBBY_PATH,
    api: MANY,
    ready: manyReady,
    act: async (page) => {
      await firstCardActions(page).click();
      await expect(page.getByRole("menuitem", { name: "删除" })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "lobby-card-menu", target: (page) => projectList(page).getByRole("listitem").first() },
  },
  {
    name: "重命名项目的对话框说明项目 ID 不变",
    path: LOBBY_PATH,
    api: MANY,
    ready: manyReady,
    act: async (page) => {
      await firstCardActions(page).click();
      await page.getByRole("menuitem", { name: "重命名" }).click();
      const dialog = page.getByRole("dialog", { name: "重命名项目" });
      await expect(dialog.getByRole("textbox", { name: "项目标题" })).toHaveValue(`${LONG_TITLE} 第 1 部`);
      await expect(dialog.getByRole("button", { name: "保存" })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "lobby-rename-dialog", target: (page) => page.getByRole("dialog") },
  },
  {
    name: "删除项目的确认，按钮留在视口内",
    path: LOBBY_PATH,
    api: MANY,
    ready: manyReady,
    act: async (page) => {
      await firstCardActions(page).click();
      await page.getByRole("menuitem", { name: "删除" }).click();
      const confirm = page.getByRole("alertdialog");
      await expect(confirm.getByRole("button", { name: "删除项目" })).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "从项目卡导出：导出范围对话框的操作按钮留在视口内",
    path: LOBBY_PATH,
    api: MANY,
    ready: manyReady,
    act: async (page) => {
      await firstCardActions(page).click();
      await page.getByRole("menuitem", { name: "导出" }).click();
      const dialog = page.getByRole("dialog", { name: "选择导出范围" });
      await expect(dialog.getByRole("button", { name: "导出" })).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "删除失败原因很长：正文可聚焦并用键盘滚动，取消仍可达",
    path: LOBBY_PATH,
    api: { ...MANY, "DELETE /api/v1/projects/project-0": {
      status: 500, body: { detail: "无法删除项目，存储系统返回诊断：".repeat(120) },
    } },
    ready: manyReady,
    act: async (page) => {
      await firstCardActions(page).click();
      await page.getByRole("menuitem", { name: "删除" }).click();
      const dialog = page.getByRole("alertdialog");
      await dialog.getByRole("button", { name: "删除项目" }).click();
      await expect(dialog.getByRole("alert")).toContainText("无法删除项目");
      const body = dialog.getByRole("region");
      await body.focus();
      await body.press("End");
      await expect.poll(() => body.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
      await expect(dialog.getByRole("button", { name: "取消" })).toBeInViewport({ ratio: 1 });
    },
  },
]);
