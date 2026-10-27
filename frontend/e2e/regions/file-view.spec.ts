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
const project = recorded<{ project: { episodes: Record<string, unknown>[] } }>("project-demo.json");
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
          download_only: false,
        };
      }),
    },
  },
};

// 降级恢复过的剪辑时间线记录：只剩结构文件，只能下载；另一条剪辑时间线记录按对象名显示。
const TIMELINE_DOCUMENT = "edit_timelines/episode_1/tl-0000abcd.json";
const TRASH_DEGRADED: ApiOverrides = {
  ...EVENT_STREAM,
  "GET /api/v1/projects/demo/trash": {
    status: 200,
    body: {
      ...trash,
      total_size: 4096,
      records: ["tl-0000abcd", "tl-0000beef"].map((id, i) => ({
        id: `20261024T08000${i}Z-0000000${i}`,
        object: { kind: "edit_timeline", id, episode: 1, name: i === 0 ? "初剪" : "完整版" },
        files: i === 0 ? [TIMELINE_DOCUMENT] : [`edit_timelines/episode_1/${id}.json`],
        schema_version: 17,
        deleted_at: "2026-10-24T08:00:00+00:00",
        origin: "user",
        size: 2048,
        has_snapshot: i !== 0,
        download_only: i === 0,
      })),
    },
  },
};

const sections = (page: Page) => page.getByRole("tablist", { name: "文件视图分区" });
const LONG_EPISODE_TITLE = "雨夜里的旧城茶馆与修表匠的约定".repeat(8);

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
    name: "多选未引用文件：选择数量、取消选择与受保护文件",
    path: PATH,
    api: STRESS,
    ready: (page) => tree(page).getByRole("button", { name: /storyboards/ }).waitFor(),
    act: async (page) => {
      const nav = tree(page);
      await nav.getByRole("button", { name: /storyboards/ }).click();
      await nav.getByRole("checkbox", { name: `选择 ${LONG_NAME}_1.png` }).check();
      await nav.getByRole("checkbox", { name: `选择 ${LONG_NAME}_4.png` }).check();
      await expect(nav.getByRole("checkbox", { name: `选择 ${LONG_NAME}_2.png` })).toHaveCount(0);
      await expect(nav.getByRole("button", { name: "移入项目回收站（2）" })).toBeEnabled();
      await nav.getByRole("button", { name: /storyboards/ }).click();
      await expect(nav.getByRole("button", { name: "移入项目回收站（2）" })).toBeEnabled();
      await nav.getByRole("button", { name: "取消选择" }).click();
      await expect(nav.getByRole("button", { name: "移入项目回收站（2）" })).toHaveCount(0);
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
  {
    name: "项目回收站分区：长集名不遮住删除时的位置和正文",
    path: PATH,
    api: {
      ...EVENT_STREAM,
      "GET /api/v1/projects/demo": {
        status: 200,
        body: { ...project, project: { ...project.project, episodes: [{ ...project.project.episodes[0], title: LONG_EPISODE_TITLE }] } },
      },
      "GET /api/v1/projects/demo/trash": {
        status: 200,
        body: {
          ...trash,
          total_size: 0,
          records: [5, 6].map((position) => ({
            id: `deleted-shot-${position}`,
            object: { kind: "script_entry", id: `E1S0${position + 2}`, episode: 1, item_kind: "segments", item_position: position, item_excerpt: "他推开门…" },
            files: [], schema_version: 19, deleted_at: "2026-10-24T08:00:00+00:00", origin: "user", size: 0, has_snapshot: true, download_only: false,
          })),
        },
      },
    },
    ready: (page) => sections(page).waitFor(),
    act: async (page) => {
      await sections(page).getByRole("tab", { name: "项目回收站" }).click();
      for (const position of [5, 6]) {
        const title = page.getByText(`分镜 ${LONG_EPISODE_TITLE} · ${position}：他推开门…`, { exact: true });
        await expect(title).toBeVisible();
        await expect.poll(() => title.evaluate((el) => el.scrollWidth <= el.clientWidth && el.scrollHeight <= el.clientHeight)).toBe(true);
      }
    },
  },
  {
    name: "项目回收站分区：降级恢复后只能下载的记录",
    path: PATH,
    api: TRASH_DEGRADED,
    ready: (page) => sections(page).waitFor(),
    act: async (page) => {
      await sections(page).getByRole("tab", { name: "项目回收站" }).click();
      await expect(page.getByText("剪辑时间线「初剪」（第 1 集）")).toBeVisible();
      await expect(page.getByText("剪辑时间线「完整版」（第 1 集）")).toBeVisible();
      await expect(page.getByText("无法按原结构恢复，以下结构文件只能下载")).toBeVisible();
      await expect(page.getByRole("button", { name: `下载 ${TIMELINE_DOCUMENT}` })).toBeVisible();
      // 只能下载的记录没有「恢复」，另一条照常可以恢复
      await expect(page.getByRole("button", { name: "恢复" })).toHaveCount(1);
    },
  },
]);
