import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Locator, Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { RECORDED_DIR, type RecordedResponse } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 工作区顶栏与侧栏集列表：项目切换器、通知面板、项目内未知路径的空状态与集的键盘排序。
const CHARACTERS_PATH = "/app/projects/demo/characters";
// 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在录制的项目数据上。
const EVENT_STREAM: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

function recorded<T>(file: string): T {
  return (JSON.parse(readFileSync(join(RECORDED_DIR, `${file}.json`), "utf8")) as RecordedResponse).body as T;
}

interface RecordedProject {
  project: { title: string; episodes: Record<string, unknown>[] } & Record<string, unknown>;
}
interface RecordedProjects {
  projects: ({ name: string; title: string } & Record<string, unknown>)[];
}

const LONG_TITLE = "雾港来信：一座海边小城里，邮差在三十年后收到了寄给自己的信，于是沿着信里的地址一路找回童年";
const recordedProject = recorded<RecordedProject>("project-demo");
const [firstProject] = recorded<RecordedProjects>("projects").projects;
const [firstEpisode] = recordedProject.project.episodes;

// 当前项目名很长、项目很多且名字都长：切换器列表要在弹层里滚动，「← 项目」不能被挤得换行。
const MANY_PROJECTS: ApiOverrides = {
  ...EVENT_STREAM,
  "GET /api/v1/projects/demo": {
    status: 200,
    body: { ...recordedProject, project: { ...recordedProject.project, title: LONG_TITLE } },
  },
  "GET /api/v1/projects": {
    status: 200,
    body: {
      projects: [
        { ...firstProject, title: LONG_TITLE },
        ...Array.from({ length: 30 }, (_, i) => ({
          ...firstProject,
          name: `project-${i + 1}`,
          title: `第 ${i + 1} 号项目：夜色压低了城墙的轮廓，巡夜人提着灯从箭楼下走过`,
        })),
      ],
    },
  },
};

const EPISODE_TITLE = (i: number) => `第 ${i} 集：夜色压低了城墙的轮廓，巡夜人提着灯从箭楼下走过`;
const MANY_EPISODES: ApiOverrides = {
  ...EVENT_STREAM,
  "GET /api/v1/projects/demo": {
    status: 200,
    body: {
      ...recordedProject,
      project: {
        ...recordedProject.project,
        episodes: Array.from({ length: 12 }, (_, i) => ({
          ...firstEpisode,
          episode: i + 1,
          title: EPISODE_TITLE(i + 1),
          script_file: `scripts/episode_${i + 1}.json`,
        })),
      },
    },
  },
  "POST /api/v1/projects/demo/episodes/2/move": { status: 200, body: { success: true } },
};

// 导出失败会记入工作区通知；用它在页面里攒出多条长通知。
const EXPORT_FAILS: ApiOverrides = {
  ...EVENT_STREAM,
  "POST /api/v1/projects/demo/export/token?scope=current": {
    status: 500,
    body: {
      detail:
        "打包时读取 projects/demo/episodes/episode_12/storyboards/scene_07/shot_03/variants/final_render_with_an_unusually_long_file_name_v12.png 失败，磁盘可能已满或文件被其他程序占用。",
    },
  },
};

const agentPanel = (page: Page) => page.getByRole("complementary", { name: "Agent 面板" });
const switcher = (page: Page) => page.getByRole("button", { name: /^切换项目/ });
const popover = (page: Page) => page.locator('[data-slot="popover-content"]');
const bell = (page: Page) => page.getByRole("button", { name: /^工作区通知/ });

async function shellReady(page: Page) {
  await agentPanel(page).waitFor();
  await switcher(page).waitFor();
}

/** 弹层进场动画结束后再探测。 */
async function settled(locator: Locator) {
  await locator.evaluate((element) =>
    Promise.allSettled(element.getAnimations({ subtree: true }).map((animation) => animation.finished)),
  );
}

/** 紧凑档侧栏自动收为图标栏，先展开才能看到集列表。 */
async function expandSidebar(page: Page) {
  const expand = page.getByRole("button", { name: "展开侧栏" });
  if (await expand.isVisible()) await expand.click();
}

async function failExport(page: Page) {
  await page.getByRole("button", { name: "导出项目归档" }).click();
  const dialog = page.getByRole("dialog", { name: "选择导出范围" });
  await dialog.getByRole("button", { name: "导出", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("button", { name: "导出项目归档" })).toBeEnabled();
}

defineRegionScenarios("工作区顶栏", [
  {
    name: "项目名很长时「← 项目」不换行，打开项目切换器后列表在弹层里滚动",
    path: CHARACTERS_PATH,
    api: MANY_PROJECTS,
    ready: shellReady,
    act: async (page) => {
      const back = page.getByRole("link", { name: "项目", exact: true });
      // 单行按钮高 28px，换行后会更高
      expect((await back.boundingBox())?.height).toBeLessThanOrEqual(28);
      await expect(back).toBeInViewport({ ratio: 1 });

      await switcher(page).click();
      const search = page.getByRole("combobox", { name: "搜索项目" });
      await expect(search).toBeFocused();
      await expect(page.getByRole("option", { name: /第 30 号项目/ })).toBeAttached();
      const all = page.getByRole("link", { name: "全部项目" });
      await all.scrollIntoViewIfNeeded();
      await expect(all).toBeInViewport({ ratio: 1 });
      await search.fill("第 7 号");
      await expect(page.getByRole("option", { name: /第 7 号项目/ })).toBeVisible();
      await search.fill("");
      await settled(popover(page));
    },
    screenshot: { name: "workspace-header-switcher", target: popover },
  },
  {
    name: "没有通知时打开通知面板",
    path: CHARACTERS_PATH,
    api: EVENT_STREAM,
    ready: shellReady,
    act: async (page) => {
      await bell(page).click();
      await expect(page.getByText("当前没有通知")).toBeInViewport({ ratio: 1 });
      await settled(popover(page));
    },
  },
  {
    name: "多条长通知时打开通知面板：角标立即清零，未读高亮保留，列表在面板里滚动",
    path: CHARACTERS_PATH,
    api: EXPORT_FAILS,
    ready: shellReady,
    act: async (page) => {
      for (let i = 0; i < 6; i++) await failExport(page);
      // 提示 5 秒后自动消失；等它们走完，截图里只有通知面板
      await expect(page.locator('[data-slot="toast"]')).toHaveCount(0, { timeout: 15_000 });
      await expect(bell(page)).toHaveAccessibleName("工作区通知，未读 6 条");

      await bell(page).click();
      const panel = page.getByRole("dialog", { name: "工作区通知" });
      await expect(bell(page)).toHaveAccessibleName("工作区通知");
      const last = panel.getByRole("listitem").last();
      await last.scrollIntoViewIfNeeded();
      await expect(last).toBeInViewport();
      await expect(panel.getByRole("heading", { name: "工作区通知" })).toBeInViewport({ ratio: 1 });
      await settled(popover(page));
    },
    screenshot: { name: "workspace-header-notifications", target: popover },
  },
  {
    name: "项目内不存在的路径在画布里显示空状态，外壳保留",
    path: "/app/projects/demo/unknown",
    api: EVENT_STREAM,
    ready: async (page) => {
      await shellReady(page);
      await page.getByRole("heading", { name: "这个页面不存在" }).waitFor();
    },
    act: async (page) => {
      await expect(page.getByRole("link", { name: "回到概览" })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "workspace-not-found", target: (page) => page.getByRole("main") },
  },
  { name: "旧入口 lorebook 在画布里显示空状态", path: "/app/projects/demo/lorebook", api: EVENT_STREAM, ready: notFoundReady },
  { name: "旧入口 clues 在画布里显示空状态", path: "/app/projects/demo/clues", api: EVENT_STREAM, ready: notFoundReady },
  {
    name: "侧栏集只用键盘调整播出顺序",
    path: CHARACTERS_PATH,
    api: MANY_EPISODES,
    ready: async (page) => {
      await shellReady(page);
      await expandSidebar(page);
      await page.getByRole("button", { name: `调整「${EPISODE_TITLE(2)}」的顺序` }).waitFor({ state: "attached" });
    },
    act: async (page) => {
      const saved = page.waitForRequest(
        (request) => request.method() === "POST" && request.url().endsWith("/api/v1/projects/demo/episodes/2/move"),
      );
      await page.getByRole("button", { name: `调整「${EPISODE_TITLE(2)}」的顺序` }).focus();
      await page.keyboard.press("Space");
      await expect(page.getByText(`已拿起「${EPISODE_TITLE(2)}」，位于第 2 项，共 12 项。`)).toBeAttached();
      // dnd-kit 拿起后要等测量完成才响应方向键，之前的按键会被丢掉；按到播报移动为止。
      // 目标是第 1 项，已在最前时再按上移不会继续移动，重按是安全的。
      await expect(async () => {
        await page.keyboard.press("ArrowUp");
        await expect(page.getByText(`「${EPISODE_TITLE(2)}」移到第 1 项，共 12 项。`)).toBeAttached({ timeout: 200 });
      }).toPass();
      await page.keyboard.press("Space");
      expect((await saved).postDataJSON()).toEqual({ after: null });
    },
  },
]);

async function notFoundReady(page: Page) {
  await shellReady(page);
  await page.getByRole("heading", { name: "这个页面不存在" }).waitFor();
}
