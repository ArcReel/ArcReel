import type { Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { recorded } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 项目「文件」视图：左侧目录树按需展开，右侧是选中文件的角色、预览与入口；两栏各自滚动。
const PATH = "/app/projects/demo/files";
const LISTING = "GET /api/v1/projects/demo/file-view";
// 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在录制的项目数据上。
const EVENT_STREAM: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

interface Listing {
  path: string;
  entries: Record<string, unknown>[];
}

interface TrashListing {
  records: Record<string, unknown>[];
  total_size: number;
  retention_days: number;
}

const root = recorded<Listing>("project-demo-file-view.json");
const trash = recorded<TrashListing>("project-demo-trash.json");
const scripts = recorded<Listing>("project-demo-file-view-scripts.json");
const scriptFile = scripts.entries[0];

const tree = (page: Page) => page.getByRole("navigation", { name: "项目文件" });

// 压力变体：分镜目录里两百张图，文件名很长、角色各异；剧本正文三千行。
const LONG_NAME = "scene_E12S0001_雨夜里的旧城茶馆与修表匠的约定_第三稿_含删去的支线与批注";
const STORYBOARDS: Listing = {
  path: "storyboards",
  entries: Array.from({ length: 200 }, (_, i) => ({
    name: `${LONG_NAME}_${i + 1}.png`,
    path: `storyboards/${LONG_NAME}_${i + 1}.png`,
    type: "file",
    size: 1_234_567 + i,
    modified_at: "2026-01-01T08:00:00+00:00",
    role: i % 3 === 0 ? "unreferenced" : "referenced",
    owner: i % 3 === 0 ? null : { kind: "segment", id: `E12S${String(i + 1).padStart(4, "0")}`, episode: 12 },
  })),
};
const LONG_SCRIPT = JSON.stringify({
  segments: Array.from({ length: 600 }, (_, i) => ({
    segment_id: `E1S${i + 1}`,
    novel_text: "茶馆的伙计把最后一张条凳搬进门里，听见远处传来三声更鼓。".repeat(3),
  })),
});

const STRESS: ApiOverrides = {
  ...EVENT_STREAM,
  [`${LISTING}?path=`]: {
    status: 200,
    body: {
      ...root,
      entries: root.entries.map((entry) => (entry.name === "storyboards" ? { ...entry, size: 2_469_134_000 } : entry)),
    },
  },
  [`${LISTING}?path=storyboards`]: { status: 200, body: STORYBOARDS },
  [`GET /api/v1/projects/demo/file-view/content?path=${encodeURIComponent(String(scriptFile.path))}`]: {
    status: 200,
    body: LONG_SCRIPT,
  },
};

// 压力变体：项目回收站里一百二十条删除记录，路径很长，发起方交替。
const TRASH_STRESS: ApiOverrides = {
  ...EVENT_STREAM,
  "GET /api/v1/projects/demo/trash": {
    status: 200,
    body: {
      ...trash,
      total_size: 9_876_543_210,
      records: Array.from({ length: 120 }, (_, i) => {
        const path = `storyboards/${LONG_NAME}_${i + 1}.png`;
        return {
          id: `20261024T0800${String(i % 60).padStart(2, "0")}Z-${String(i).padStart(8, "0")}`,
          object: { kind: "file", path },
          files: [path],
          schema_version: 18,
          deleted_at: "2026-10-24T08:00:00+00:00",
          origin: i % 2 === 0 ? "user" : "agent",
          size: 1_234_567 + i,
          has_snapshot: false,
        };
      }),
    },
  },
};

const sections = (page: Page) => page.getByRole("tablist", { name: "文件视图分区" });

defineRegionScenarios("文件视图", [
  {
    name: "项目根：目录树与空的详情栏",
    path: PATH,
    api: EVENT_STREAM,
    ready: (page) => tree(page).getByRole("button", { name: /project\.json/ }).waitFor(),
    act: async (page) => {
      await expect(page.getByText("选择一个文件查看详情")).toBeVisible();
    },
  },
  {
    name: "展开目录、选中剧本：显示角色、所属集入口与 JSON 预览",
    path: PATH,
    api: {
      ...EVENT_STREAM,
      [`GET /api/v1/projects/demo/file-view/content?path=${encodeURIComponent(String(scriptFile.path))}`]: {
        status: 200,
        body: JSON.stringify({ episode: 1, segments: [] }),
      },
    },
    ready: (page) => tree(page).getByRole("button", { name: /scripts/ }).waitFor(),
    act: async (page) => {
      await tree(page).getByRole("button", { name: /scripts/ }).click();
      await tree(page).getByRole("button", { name: new RegExp(String(scriptFile.name)) }).click();
      await expect(page.getByRole("heading", { level: 2, name: String(scriptFile.name) })).toBeVisible();
      await expect(page.getByRole("link", { name: "打开这一集" })).toBeVisible();
      await expect(page.getByLabel(`${String(scriptFile.name)} 的内容`)).toContainText('"segments": []');
    },
    screenshot: { name: "file-view-script", target: (page) => page.getByRole("main") },
  },
  {
    name: "大目录与长正文（压力）：目录树与预览各自滚动",
    path: PATH,
    api: STRESS,
    slow: true,
    ready: (page) => tree(page).getByRole("button", { name: /storyboards/ }).waitFor(),
    act: async (page) => {
      const nav = tree(page);
      await nav.getByRole("button", { name: /storyboards/ }).click();
      await nav.getByRole("button", { name: /scripts/ }).click();
      await nav.getByRole("button", { name: new RegExp(String(scriptFile.name)) }).click();
      const preview = page.getByLabel(`${String(scriptFile.name)} 的内容`);
      await expect(preview).toContainText("E1S600");
      await expect.poll(() => nav.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
      await nav.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      await expect(nav.getByRole("button", { name: new RegExp(`${LONG_NAME}_200\\.png`) })).toBeInViewport();
      // 目录树滚到底不带动详情栏，详情栏的页头仍在原处
      await expect(page.getByRole("heading", { level: 2, name: String(scriptFile.name) })).toBeInViewport();
    },
  },
  {
    name: "项目回收站分区：没有删除记录",
    path: PATH,
    api: EVENT_STREAM,
    ready: (page) => sections(page).waitFor(),
    act: async (page) => {
      await sections(page).getByRole("tab", { name: "项目回收站" }).click();
      await expect(page.getByText("项目回收站是空的")).toBeVisible();
      await expect(page.getByRole("button", { name: "清空项目回收站" })).toBeDisabled();
    },
  },
  {
    name: "项目回收站分区：多条删除记录与清空确认（压力）",
    path: PATH,
    api: TRASH_STRESS,
    slow: true,
    ready: (page) => sections(page).waitFor(),
    act: async (page) => {
      await sections(page).getByRole("tab", { name: "项目回收站" }).click();
      await expect(page.getByText(/共 120 条删除记录/)).toBeVisible();
      await expect(page.getByRole("button", { name: "恢复" })).toHaveCount(120);
      await page.getByRole("button", { name: "清空项目回收站" }).click();
      const dialog = page.getByRole("alertdialog");
      await expect(dialog.getByText("120 条删除记录将被彻底删除，无法恢复。")).toBeVisible();
      await expect(dialog.getByRole("button", { name: "取消" })).toBeFocused();
      await page.keyboard.press("Escape");
      await expect(dialog).toBeHidden();
    },
    screenshot: { name: "file-view-trash", target: (page) => page.getByRole("main") },
  },
]);
