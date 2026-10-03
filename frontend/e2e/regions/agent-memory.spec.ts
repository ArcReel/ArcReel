import type { Locator, Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 记忆编辑器：全局设置「Agent 记忆」（全出血档，二级栏列文件、详情栏是编辑器，两栏各自滚动）
// 与项目设置「项目记忆」分页（限宽页里的文件列表加同一个编辑器，随页面主体滚动）。
// 每个文件是一个编辑单元，未保存修改显示在编辑框下方的内联提示条里。

const USER_MEMORY = "/api/v1/agent/memory";
const PROJECT_MEMORY = "/api/v1/projects/demo/agent-memory";

// 压力变体：索引超出行数上限，三十个主题文件，名称与说明都很长；索引正文三百行，含很长的行。
const TOPICS = Array.from({ length: 30 }, (_, i) => ({
  name: `feedback-${String(i + 1).padStart(2, "0")}-keep-the-original-plot-and-character-voices-when-revising.md`,
  size: 640,
  modified_at: `2026-0${(i % 9) + 1}-1${i % 10}T02:00:00+00:00`,
  frontmatter: {
    name: `feedback-${i + 1}`,
    description: `第 ${i + 1} 条改稿要求：保留原文情节与人物口吻，只调整节奏、镜头衔接和台词长度，不新增支线角色`,
    type: (["user", "feedback", "project", "reference"] as const)[i % 4],
  },
}));

const LONG_INDEX = Array.from(
  { length: 300 },
  (_, i) =>
    `- [第 ${i + 1} 条记忆](feedback-${String((i % 30) + 1).padStart(2, "0")}.md) — 创作者在第 ${i + 1} 次会话里提出的要求，包括画幅、配色、节奏与台词风格的偏好，Agent 需要在每次改稿前读取`,
).join("\n");

function manyFiles(base: string, dir: string): ApiOverrides {
  return {
    [`GET ${base}`]: {
      status: 200,
      body: {
        path: dir,
        index: { exists: true, line_count: 300, byte_size: 48000, over_limit: true },
        files: TOPICS,
      },
    },
    [`GET ${base}/files/MEMORY.md`]: { status: 200, body: LONG_INDEX },
  };
}

const USER_FILES = manyFiles(USER_MEMORY, "/home/creator/.arcreel/data/users/default/memory");
const PROJECT_FILES = manyFiles(PROJECT_MEMORY, "/home/creator/.arcreel/data/projects/demo/.arcreel/memory");

/** 等弹层进出场与按钮配色过渡结束：axe 会把过渡中间色判为对比度不足。过渡被取消时 finished 以 AbortError 拒绝。 */
async function settle(page: Page) {
  await page.evaluate(() => Promise.allSettled(document.getAnimations().map((animation) => animation.finished)));
}

const editor = (page: Page) => page.getByRole("textbox", { name: "MEMORY.md" });

/** 二级栏（标准档或紧凑档，只有一份可见）。 */
const rail = (page: Page) => page.getByRole("navigation", { name: "记忆文件" });

async function scrollToBottom(locator: Locator) {
  await locator.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
}

async function editIndex(page: Page) {
  await editor(page).click();
  await editor(page).press("ControlOrMeta+End");
  await editor(page).pressSequentially("\n- 新条目");
  await expect(page.getByText("有未保存的修改")).toBeVisible();
}

defineRegionScenarios("Agent 记忆", [
  {
    name: "没有记忆文件：详情栏是新建表单",
    path: "/app/settings?section=agent-memory",
    ready: (page) => page.getByRole("heading", { level: 2, name: "新建记忆文件" }).waitFor(),
    act: async (page) => {
      await expect(page.getByText(/Agent 会在创作过程中自动记录/)).toBeVisible();
    },
  },
  {
    name: "文件很多、索引很长（压力）：文件列表与编辑框各自滚动",
    path: "/app/settings?section=agent-memory",
    api: USER_FILES,
    ready: async (page) => {
      await expect(editor(page)).toHaveValue(/第 300 条记忆/);
    },
    act: async (page) => {
      const nav = rail(page);
      const box = editor(page);
      await expect.poll(() => nav.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
      await expect.poll(() => box.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);

      await scrollToBottom(nav);
      await expect(nav.getByRole("link", { name: /新建记忆文件/ }).filter({ visible: true })).toBeInViewport({
        ratio: 1,
      });
      // 列表滚到底不带动编辑框，编辑框滚到底也不带动列表
      expect(await box.evaluate((el) => el.scrollTop)).toBe(0);
      await scrollToBottom(box);
      expect(await nav.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
      await expect(page.getByRole("heading", { level: 2, name: "MEMORY.md" })).toBeInViewport();
    },
    screenshot: { name: "agent-memory-editor", target: (page) => page.getByRole("main") },
  },
  {
    name: "改动索引（压力）：内联提示条在编辑框下方完整可见",
    path: "/app/settings?section=agent-memory",
    api: USER_FILES,
    ready: async (page) => {
      await expect(editor(page)).toHaveValue(/第 300 条记忆/);
    },
    act: async (page) => {
      await editIndex(page);
      await expect(page.getByRole("button", { name: "保存" })).toBeInViewport({ ratio: 1 });
      await expect(page.getByRole("button", { name: "放弃修改" })).toBeInViewport({ ratio: 1 });
      await settle(page);
    },
    screenshot: { name: "agent-memory-unsaved", target: (page) => page.getByRole("main") },
  },
  {
    name: "打开更多操作菜单，菜单留在视口内",
    path: "/app/settings?section=agent-memory",
    api: USER_FILES,
    ready: async (page) => {
      await expect(editor(page)).toHaveValue(/第 300 条记忆/);
    },
    act: async (page) => {
      await page.getByRole("button", { name: "更多操作" }).click();
      await expect(page.getByRole("menuitem", { name: "清空全部记忆" })).toBeInViewport({ ratio: 1 });
      await settle(page);
    },
  },
  {
    name: "删除确认：标题与按钮完整可见",
    path: "/app/settings?section=agent-memory",
    api: USER_FILES,
    ready: async (page) => {
      await expect(editor(page)).toHaveValue(/第 300 条记忆/);
    },
    act: async (page) => {
      await page.getByRole("button", { name: "删除" }).click();
      const dialog = page.getByRole("alertdialog", { name: "删除 MEMORY.md？" });
      await dialog.waitFor();
      await settle(page);
      await expect(dialog.getByRole("button", { name: "删除" })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "agent-memory-delete-dialog", target: (page) => page.getByRole("alertdialog") },
  },
]);

defineRegionScenarios("项目记忆", [
  {
    name: "文件很多、索引很长（压力）：改动后提示条可见，页面滚到底列表末尾的新建入口可见",
    path: "/app/projects/demo/settings?tab=memory",
    api: PROJECT_FILES,
    ready: async (page) => {
      await expect(editor(page)).toHaveValue(/第 300 条记忆/);
    },
    act: async (page) => {
      await editIndex(page);
      await expect(page.getByRole("button", { name: "保存" })).toBeVisible();
      // 记忆文件自己保存，外壳底行不出现项目设置的保存栏
      await expect(page.getByRole("button", { name: "保存" })).toHaveCount(1);
      await settle(page);
      await page.getByRole("main").evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      await expect(page.getByRole("link", { name: "新建记忆文件" })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "project-memory", target: (page) => page.getByRole("main") },
  },
]);
