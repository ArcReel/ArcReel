import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Locator, Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { RECORDED_DIR, type RecordedResponse } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 项目「分集」视图：页头工具行、集目录与原文、方案栏，以及上传、新建、未登记文件等弹层。
const PATH = "/app/projects/demo/episodes";
// 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在录制的项目数据上。
const API: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

// 标准档与紧凑档的分界，与外壳的媒体查询一致；紧凑档的 Agent 面板盖在画布右侧。
const STANDARD_TIER_MIN_WIDTH = 1280;

interface RecordedProject {
  project: { episodes: Record<string, unknown>[] };
}

const recordedProject = (
  JSON.parse(readFileSync(join(RECORDED_DIR, "project-demo.json"), "utf8")) as RecordedResponse
).body as RecordedProject;
const [firstEpisode] = recordedProject.project.episodes;

// ---------------------------------------------------------------------------
// 压力数据：两个整本源文文件、几十集、长标题与长文件名，切出集之间留一段空当，结尾还有未分集的原文
// ---------------------------------------------------------------------------

const PARAGRAPHS = [
  "雨从傍晚一直下到深夜。巡夜人提着灯从箭楼下走过，灯影在湿透的石板上晃成一片，城墙的轮廓被夜色压得很低。",
  "茶馆的伙计把最后一张条凳搬进门里，听见远处传来三声更鼓。林夕把账本合上，抬头看了一眼挂在柜台后面的旧罗盘，指针又一次转向了门口。",
  "陈默推门进来时带进一股潮气。他把湿透的伞靠在门边，左手腕上的旧疤在灯下泛着白，像一道没有愈合的裂缝。",
  "「今晚不做生意了。」林夕说。陈默没有答话，只是把一块停了的怀表放在柜台上，表盖内侧刻着一行已经磨平的小字。",
];

function episodeText(index: number): string {
  return Array.from({ length: 3 + (index % 3) }, (_, i) => PARAGRAPHS[(index + i) % PARAGRAPHS.length]).join("\n");
}

const length = (text: string) => [...text].length;

interface Segment {
  kind: "episode" | "unsplit";
  start: number;
  end: number;
  text: string;
  episode: number | null;
  gap: boolean;
  units: number;
  continued: boolean;
  continues: boolean;
}

interface EpisodeInfo {
  episode: number;
  origin: "whole_source" | "own" | "none";
  placed: boolean;
  source_file: string | null;
  end_file: string | null;
  units: number | null;
  spoken_seconds: number | null;
  first_sentence: string;
  last_sentence: string;
  source_kind: null;
}

const FILES = [
  {
    source_file: "source/上卷：雨夜里的旧城茶馆与修表匠的约定（作者手改第三稿，含删去的支线与批注）.txt",
    name: "上卷：雨夜里的旧城茶馆与修表匠的约定（作者手改第三稿，含删去的支线与批注）.txt",
    original_filename: "上卷 第三稿 批注版 最终 最终.docx",
    episodes: 14,
    // 第 6 集删掉后留下的空当
    gapAfter: 5,
    tail: 3,
  },
  { source_file: "source/下卷.txt", name: "下卷.txt", original_filename: null, episodes: 30, gapAfter: null, tail: 0 },
];

const infos: EpisodeInfo[] = [];
let nextEpisode = 1;
const files = FILES.map((spec) => {
  const segments: Segment[] = [];
  let offset = 0;
  let cutUnits = 0;
  const push = (kind: Segment["kind"], text: string, episode: number | null, gap: boolean) => {
    const units = length(text);
    segments.push({ kind, start: offset, end: offset + units, text, episode, gap, units, continued: false, continues: false });
    offset += units;
    return units;
  };
  for (let i = 0; i < spec.episodes; i += 1) {
    const id = nextEpisode;
    nextEpisode += 1;
    const text = episodeText(id);
    const units = push("episode", text, id, false);
    cutUnits += units;
    infos.push({
      episode: id,
      origin: "whole_source",
      placed: true,
      source_file: spec.source_file,
      end_file: spec.source_file,
      units,
      spoken_seconds: Math.round(units / 4),
      first_sentence: PARAGRAPHS[id % PARAGRAPHS.length].split("。")[0],
      last_sentence: PARAGRAPHS[(id + 2) % PARAGRAPHS.length].split("。")[0],
      source_kind: null,
    });
    if (spec.gapAfter === i + 1) push("unsplit", episodeText(id + 7), null, true);
  }
  for (let i = 0; i < spec.tail; i += 1) push("unsplit", episodeText(i), null, false);
  return {
    source_file: spec.source_file,
    name: spec.name,
    original_filename: spec.original_filename,
    missing: false,
    changed_outside: false,
    length: offset,
    units: offset,
    cut_units: cutUnits,
    segments,
    source_kind: null,
  };
});

// 原文不在整本源文里的集：两集自带原文、一集还没有原文
for (const origin of ["own", "own", "none"] as const) {
  infos.push({
    episode: nextEpisode,
    origin,
    placed: false,
    source_file: null,
    end_file: null,
    units: origin === "own" ? 1820 : null,
    spoken_seconds: origin === "own" ? 455 : null,
    first_sentence: "",
    last_sentence: "",
    source_kind: null,
  });
  nextEpisode += 1;
}

const TITLES = ["雨夜", "停了的怀表", "罗盘指向门口", "箭楼下的灯", "修表匠的旧疤", "茶馆打烊之后"];
const episodeMeta = infos.map((info, index) => ({
  ...firstEpisode,
  episode: info.episode,
  title:
    index % 7 === 3
      ? `${TITLES[index % TITLES.length]}：夜色压低了城墙的轮廓，巡夜人提着灯从箭楼下走过，风把旗子吹得猎猎作响`
      : TITLES[index % TITLES.length],
  script_file: `scripts/episode_${info.episode}.json`,
  source_origin: info.origin,
  ...(index === 2 ? { ledger_status: "stale" } : {}),
}));

const totalUnits = files.reduce((sum, file) => sum + file.units, 0);
const cutUnits = files.reduce((sum, file) => sum + file.cut_units, 0);

const VIEW = {
  unit: "chars",
  units: totalUnits,
  cut_units: cutUnits,
  files,
  episodes: infos,
  unregistered: [
    { name: "旧稿：雨夜里的旧城茶馆（删掉的开头与两条支线，留作参考）.txt", size: 18342, can_join_whole_source: true },
    { name: "_作者批注.txt", size: 2048, can_join_whole_source: false },
    { name: "番外：修表匠年轻时在南方码头当学徒的那几年.md", size: 9120, can_join_whole_source: true },
    { name: "episode_99.txt", size: 512, can_join_whole_source: false },
  ],
  replan: null,
  external_changes: [],
};

const POPULATED: ApiOverrides = {
  ...API,
  "GET /api/v1/projects/demo": {
    status: 200,
    body: {
      ...recordedProject,
      project: {
        ...recordedProject.project,
        episodes: episodeMeta,
        whole_source_files: files.map((file) => ({ source_file: file.source_file })),
      },
    },
  },
  "GET /api/v1/projects/demo/episodes-view": { status: 200, body: VIEW },
};

// 从第 7 集起重新规划：新方案把第 7–9 集重新切成四集
const [upper] = files;
const replanFrom = upper.segments.find((segment) => segment.episode === 7);
const replanTo = upper.segments.find((segment) => segment.episode === 9);
if (!replanFrom || !replanTo) throw new Error("压力数据里缺少第 7–9 集");
const replanUnits = replanTo.end - replanFrom.start;
const quarter = Math.floor(replanUnits / 4);
const REPLAN = {
  id: "cand-1",
  episode: 7,
  instructions: "节奏放慢，每集停在一个悬念上；雨夜那场戏单独成集，不要和修表匠回忆的段落拼在一起。",
  complete: true,
  interrupted: null,
  stale: null,
  start: { source_file: upper.source_file, offset: replanFrom.start },
  end: { source_file: upper.source_file, offset: replanTo.end },
  old_count: 3,
  new_count: 4,
  units: replanUnits,
  average_units: quarter,
  retired: [8],
  removed: [],
  needs_review: [7, 8],
  uncovered: [],
  moved: [{ episode: 45, from: 45, to: 46 }],
  episodes: Array.from({ length: 4 }, (_, i) => ({
    title: i === 1 ? "罗盘指向门口：林夕第一次意识到那块停了的怀表与茶馆的旧罗盘之间有某种联系" : `新的第 ${i + 1} 集`,
    hook: "",
    source_file: upper.source_file,
    start: replanFrom.start + i * quarter,
    end: i === 3 ? replanTo.end : replanFrom.start + (i + 1) * quarter,
    units: quarter,
    first_sentence: PARAGRAPHS[i % PARAGRAPHS.length].split("。")[0],
    last_sentence: PARAGRAPHS[(i + 1) % PARAGRAPHS.length].split("。")[0],
    same_as: i === 0 ? 7 : null,
    overlaps: i === 0 ? [7] : [8],
  })),
};

const WITH_REPLAN: ApiOverrides = {
  ...POPULATED,
  "GET /api/v1/projects/demo/episodes-view": { status: 200, body: { ...VIEW, replan: REPLAN } },
};

// ---------------------------------------------------------------------------

const canvas = (page: Page) => page.getByRole("main");
const outline = (page: Page) => page.getByRole("navigation", { name: "集目录" });
const manuscript = (page: Page) => page.getByRole("region", { name: "整本源文" });
const outlineRow = (page: Page, episode: number) => outline(page).locator(`[data-outline-episode="${episode}"] > :first-child`);
const agentToggle = (page: Page) => page.getByRole("button", { name: "Agent", exact: true });

function viewport(page: Page) {
  const size = page.viewportSize();
  if (!size) throw new Error("没有视口尺寸");
  return size;
}

async function box(locator: Locator) {
  const rect = await locator.boundingBox();
  if (!rect) throw new Error("元素不可见");
  return rect;
}

async function waitForAnimations(locator: Locator) {
  await locator.evaluate((el) => Promise.allSettled(el.getAnimations({ subtree: true }).map((a) => a.finished)));
}

/** 紧凑档的 Agent 面板盖在画布右侧：点画布右侧的控件之前先收起它。 */
async function revealCanvasRight(page: Page) {
  if (viewport(page).width >= STANDARD_TIER_MIN_WIDTH) return;
  await agentToggle(page).click();
  await expect(agentToggle(page)).toHaveAttribute("aria-pressed", "false");
}

async function populatedReady(page: Page) {
  await outline(page).waitFor();
  await manuscript(page).getByRole("article").first().waitFor();
}

/** 画布里只有集目录与原文两处在滚动；文档本身由溢出探针检查。 */
async function expectOnlyColumnsScroll(page: Page) {
  const scrolling = await canvas(page).evaluate((main) =>
    [main, ...main.querySelectorAll("*")]
      .filter((el) => {
        const style = getComputedStyle(el);
        return ["auto", "scroll"].includes(style.overflowY) && el.scrollHeight > el.clientHeight + 1;
      })
      .map((el) => el.getAttribute("aria-label")),
  );
  expect(scrolling.sort()).toEqual(["整本源文", "集目录"]);
}

defineRegionScenarios("分集视图", [
  {
    name: "没有整本源文时集目录只列其他集，原文区邀请上传",
    path: PATH,
    api: API,
    ready: async (page) => {
      await page.getByText("还没有整本源文").waitFor();
    },
    act: async (page) => {
      await expect(outline(page).getByRole("link", { name: /第 1 集/ })).toBeVisible();
    },
    screenshot: { name: "episodes-empty", target: canvas },
  },
  {
    name: "整本源文按集分段，集目录与原文各自滚动，正文行长受控",
    path: PATH,
    api: POPULATED,
    ready: populatedReady,
    act: async (page) => {
      await expectOnlyColumnsScroll(page);
      const text = await box(page.locator("[data-manuscript]"));
      // 正文栏最宽 40em：Agent 面板展开的 1440 宽下约 630px，2560 宽下也不再变宽
      expect(text.width).toBeLessThanOrEqual(641);
      if (viewport(page).width === 1440) expect(text.width).toBeGreaterThan(600);
      const rail = await box(outline(page));
      expect(rail.width).toBeGreaterThanOrEqual(240);
      expect(rail.width).toBeLessThanOrEqual(300);
    },
    screenshot: { name: "episodes-manuscript", target: canvas },
  },
  {
    name: "滚动原文时集目录高亮当前集，点击集目录让原文滚到这一集",
    path: PATH,
    api: POPULATED,
    ready: populatedReady,
    act: async (page) => {
      await page.locator('[data-episode-block="9"] header').evaluate((el) => el.scrollIntoView({ block: "start" }));
      await expect(outlineRow(page, 9)).toHaveAttribute("aria-current", "true");

      await outlineRow(page, 22).click();
      await expect(outlineRow(page, 22)).toHaveAttribute("aria-current", "true");
      const area = await box(manuscript(page));
      await expect
        .poll(async () => (await box(page.locator('[data-episode-block="22"] header'))).y - area.y)
        .toBeLessThan(40);
    },
  },
  {
    name: "集的「⋯」菜单列出单集操作",
    path: PATH,
    api: POPULATED,
    ready: populatedReady,
    act: async (page) => {
      await outline(page).locator('[data-outline-episode="3"]').getByRole("button", { name: /的操作$/ }).click();
      const menu = page.getByRole("menu");
      await expect(menu.getByRole("menuitem", { name: "从这一集开始重新规划" })).toBeVisible();
      await waitForAnimations(menu);
    },
    screenshot: { name: "episodes-episode-menu", target: (page) => page.getByRole("menu") },
  },
  {
    name: "页头的「AI 规划分集」弹层",
    path: PATH,
    api: POPULATED,
    ready: populatedReady,
    act: async (page) => {
      await revealCanvasRight(page);
      await page.getByRole("button", { name: "AI 规划剩余内容" }).click();
      const popover = page.getByRole("dialog");
      await expect(popover.getByRole("button", { name: "交给 Agent" })).toBeVisible();
      await waitForAnimations(popover);
    },
    screenshot: { name: "episodes-plan-popover", target: (page) => page.getByRole("dialog") },
  },
  {
    name: "上传原文的分体按钮展开上传方式",
    path: PATH,
    api: POPULATED,
    ready: populatedReady,
    act: async (page) => {
      await revealCanvasRight(page);
      await page.getByRole("button", { name: "选择上传方式" }).click();
      const menu = page.getByRole("menu");
      await expect(menu.getByRole("menuitem", { name: /^逐集原文/ })).toBeVisible();
      await waitForAnimations(menu);
    },
  },
  {
    name: "新建一集对话框",
    path: PATH,
    api: POPULATED,
    ready: populatedReady,
    act: async (page) => {
      await revealCanvasRight(page);
      await page.getByRole("button", { name: "新建一集", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "新建一集" });
      await expect(dialog.getByLabel("标题")).toBeVisible();
      await waitForAnimations(dialog);
    },
  },
  {
    name: "未登记文件的提示条打开处理对话框",
    path: PATH,
    api: POPULATED,
    ready: populatedReady,
    act: async (page) => {
      await expect(page.getByRole("status").filter({ hasText: "还没用上" })).toBeVisible();
      await revealCanvasRight(page);
      await page.getByRole("button", { name: "处理", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "处理还没用上的文件" });
      await expect(dialog.getByRole("listitem")).toHaveCount(4);
      await waitForAnimations(dialog);
    },
    screenshot: { name: "episodes-unregistered", target: (page) => page.getByRole("dialog") },
  },
  {
    name: "有新的分集方案时集目录让位，原文右侧是方案栏",
    path: PATH,
    api: WITH_REPLAN,
    ready: async (page) => {
      await page.getByRole("region", { name: "新的分集方案" }).waitFor();
      await manuscript(page).getByRole("article").first().waitFor();
    },
    act: async (page) => {
      await expect(outline(page)).toHaveCount(0);
      await expect(page.getByRole("button", { name: "有新的分集方案" })).toBeDisabled();
      const column = await box(page.getByRole("region", { name: "新的分集方案" }));
      expect(column.width).toBeGreaterThanOrEqual(320);
      expect(column.width).toBeLessThanOrEqual(400);
    },
    screenshot: { name: "episodes-replan", target: canvas },
  },
]);
