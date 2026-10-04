import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Locator, Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { RECORDED_DIR, type RecordedResponse } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 集页「分镜」视图：左侧分镜列表，右侧分镜详情（中栏引用、提示词、台词、对应原文，媒体栏分镜图与视频）。
const BOARD_PATH = "/app/projects/demo/episodes/1";

function recorded<T>(file: string): T {
  return (JSON.parse(readFileSync(join(RECORDED_DIR, file), "utf8")) as RecordedResponse).body as T;
}

interface RecordedProject {
  project: Record<string, unknown>;
  scripts: Record<string, Record<string, unknown>>;
}

const project = recorded<RecordedProject>("project-demo.json");
const recordedScript = project.scripts["episode_1.json"];

const shotId = (n: number) => `E1S${String(n).padStart(2, "0")}`;

// 引用图与分镜图用内联 SVG：getFileUrl 原样使用 data: 地址，不发请求，截图稳定。
function portraitSvg(hue: number) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="360" height="640" viewBox="0 0 90 160"><rect width="90" height="160" fill="hsl(${hue} 30% 22%)"/><circle cx="45" cy="62" r="20" fill="hsl(${hue} 35% 55%)"/><rect x="18" y="96" width="54" height="48" rx="6" fill="hsl(${hue} 30% 40%)"/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

function refSvg(hue: number) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="hsl(${hue} 35% 40%)"/><circle cx="32" cy="26" r="12" fill="hsl(${hue} 35% 70%)"/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

const CHARACTERS = ["林夕", "陈默", "巡夜人老周", "林夕的母亲（回忆）"];
const SCENE = "旧城茶馆二楼临街的雕花木窗与窗下那张总被老主顾占着的方桌";
const PROPS = ["青铜罗盘", "巡夜人提着的那盏蒙着油纸、灯芯总也剪不齐的旧马灯"];

const LONG_IMAGE_PROMPT =
  "竖构图中景。夜色压低了城墙的轮廓，林夕站在茶馆二楼临街的雕花木窗边，半边脸被街上马灯的暖光照亮，另半边落在阴影里；她手里攥着一枚青铜罗盘，指针微微颤动。窗外雨丝斜落，远处箭楼的剪影只剩一道深色的边，楼下巡夜人提灯走过，光斑在湿漉漉的石板路上拖出长长的倒影。色调偏冷，只有灯光是暖的，画面颗粒感明显，景深浅，焦点落在罗盘与她的手指上。";
const LONG_VIDEO_PROMPT =
  "镜头从窗外缓慢推近，穿过雨幕停在林夕的手上；罗盘指针先是轻颤，随后猛地转向箭楼方向。她抬眼望向窗外，呼吸一滞，镜头随她的视线微微上摇，楼下马灯的光斑随着巡夜人的脚步一晃一晃地移出画面。全程无硬切，节奏由慢到稍快，结尾停在她收紧的手指上。";
const LONG_SOURCE =
  "那夜的雨下得没完没了。林夕倚在茶馆二楼的窗边，看着巡夜人老周提着那盏旧马灯从箭楼下走过，灯芯总也剪不齐，光一跳一跳的。她低头去看手里的罗盘——母亲留下的唯一一件东西——指针原本死气沉沉，此刻却像被什么拽住了似的，一点一点转向城北。她忽然想起母亲临走前说过的话：等它自己动起来的那天，你就知道该往哪儿走了。楼下有人推门进来，带进一股潮湿的冷风，她没回头，只是把罗盘握得更紧了些。";

const UTTERANCES = [
  { kind: "voiceover", text: "那夜的雨下得没完没了，像是要把整座旧城都泡软。" },
  { kind: "dialogue", speaker: "林夕", text: "它动了……娘，你说的那一天，是今天吗？" },
  { kind: "dialogue", speaker: "陈默", text: "别靠窗太近，楼下那个提灯的人已经第三次从这儿经过了。" },
  { kind: "dialogue", speaker: "林夕", text: "我知道。可罗盘指的就是箭楼，我总得去看一眼。" },
  { kind: "dialogue", speaker: "巡夜人老周", text: "二楼的姑娘，雨大，早些关窗歇着吧——夜里的城北不太平。" },
  { kind: "voiceover", text: "她没有回答，只是把罗盘握得更紧了些。" },
];

const SHOT_COUNT = 40;

const scenes = Array.from({ length: SHOT_COUNT }, (_, i) => {
  const id = shotId(i + 1);
  if (i === 0) {
    return {
      scene_id: id,
      duration_seconds: 8,
      segment_break: true,
      characters_in_scene: CHARACTERS,
      scenes: [SCENE],
      props: PROPS,
      image_prompt: LONG_IMAGE_PROMPT,
      video_prompt: LONG_VIDEO_PROMPT,
      utterances: UTTERANCES,
      source_text: LONG_SOURCE,
      note: "这一镜的罗盘特写要和第 12 镜呼应，指针方向保持一致。",
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
    };
  }
  return {
    scene_id: id,
    duration_seconds: 4,
    segment_break: i % 8 === 0,
    characters_in_scene: [CHARACTERS[i % 2]],
    scenes: [],
    props: [],
    image_prompt: `${id}：雨夜旧城的街景，${CHARACTERS[i % 2]}从画面一侧走过，马灯的光落在石板路上。`,
    video_prompt: `${id}：镜头跟随人物平移，雨丝斜落。`,
    utterances: [{ kind: "dialogue", speaker: CHARACTERS[i % 2], text: `${id} 的台词。` }],
    source_text: `${id} 对应的原文。`,
  };
});

const API: ApiOverrides = {
  // 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在替换后的数据上。
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
  "GET /api/v1/projects/demo": {
    status: 200,
    body: {
      ...project,
      project: {
        ...project.project,
        content_mode: "drama",
        characters: Object.fromEntries(
          CHARACTERS.map((name, i) => [name, { description: `${name}的设定`, character_sheet: refSvg(i * 50) }]),
        ),
        scenes: { [SCENE]: { description: "茶馆二楼", scene_sheet: refSvg(200) } },
        props: Object.fromEntries(PROPS.map((name, i) => [name, { description: name, prop_sheet: refSvg(260 + i * 40) }])),
      },
      scripts: {
        "episode_1.json": { ...recordedScript, content_mode: "drama", segments: undefined, scenes },
      },
    },
  },
  "GET /api/v1/projects/demo/versions/storyboards/E1S01": {
    status: 200,
    body: {
      resource_type: "storyboards",
      resource_id: "E1S01",
      current_version: 3,
      versions: [1, 2, 3].map((version) => ({
        version,
        filename: `scene_E1S01_v${version}.png`,
        created_at: `2026-02-0${version} 21:3${version}`,
        file_size: 1024,
        is_current: version === 3,
        prompt: version === 1 ? LONG_IMAGE_PROMPT : `第 ${version} 版分镜图`,
        file_url: portraitSvg(version * 70),
        source: version === 2 ? "image_edit" : "generate",
      })),
    },
  },
  "POST /api/v1/projects/demo/script-items/E1S01/move": { status: 200, body: { success: true } },
};

const COMPACT_TIER_MAX_WIDTH = 1279;

const shotList = (page: Page) => page.getByRole("navigation", { name: "分镜列表" });
const detail = (page: Page) => page.getByRole("tabpanel");
const agentToggle = (page: Page) => page.getByRole("button", { name: "Agent", exact: true });
const storyboard = (page: Page) => page.getByRole("img", { name: "S01 分镜图" });

async function box(locator: Locator) {
  const rect = await locator.boundingBox();
  if (!rect) throw new Error("元素不可见");
  return rect;
}

function viewport(page: Page) {
  const size = page.viewportSize();
  if (!size) throw new Error("没有视口尺寸");
  return size;
}

/** 弹层带入场动画，量尺寸、跑 axe 之前等它停下；进行中的转圈等循环动画不等。 */
async function waitForEntrance(target: Locator) {
  await target.evaluate((el) =>
    Promise.allSettled(
      el
        .getAnimations({ subtree: true })
        .filter((animation) => animation.effect?.getComputedTiming().endTime !== Infinity)
        .map((animation) => animation.finished),
    ),
  );
}

async function boardReady(page: Page) {
  await shotList(page).waitFor();
  await page.getByRole("heading", { name: "引用" }).waitFor();
}

/** 紧凑档 Agent 面板盖在画布右侧：先收起再点详情右侧的控件。 */
async function clearAgentOverlay(page: Page) {
  if (viewport(page).width > COMPACT_TIER_MAX_WIDTH) return;
  await agentToggle(page).click();
  await expect(agentToggle(page)).toHaveAttribute("aria-pressed", "false");
}

defineRegionScenarios("分镜详情", [
  {
    name: "中栏先引用后提示词与台词，媒体栏放分镜图与视频",
    path: BOARD_PATH,
    api: API,
    ready: boardReady,
    act: async (page) => {
      await expect(shotList(page).getByRole("button", { name: /^S01/ })).toHaveAttribute("aria-current", "true");
      const { width } = viewport(page);
      // 1440 宽、Agent 面板展开时，引用与台词同屏可见
      if (width === 1440) {
        await expect(agentToggle(page)).toHaveAttribute("aria-pressed", "true");
        await expect(page.getByRole("heading", { name: "引用" })).toBeInViewport();
        await expect(page.getByText(UTTERANCES[1].text)).toBeInViewport();
      }
      // 1920 宽时媒体栏约 660px，分镜图与视频并排
      if (width === 1920) {
        const image = await box(storyboard(page));
        const video = await box(page.getByRole("heading", { name: "视频", exact: true }));
        expect(video.x).toBeGreaterThan(image.x + image.width - 1);
        expect(Math.abs(video.y - image.y)).toBeLessThan(80);
      }
      // 对应原文默认收起
      await expect(page.getByRole("button", { name: "对应原文与参考" })).toHaveAttribute("aria-expanded", "false");
      // 区域截图不带紧凑档盖在画布上的 Agent 面板
      await clearAgentOverlay(page);
    },
    screenshot: { name: "shot-detail-board", target: detail },
  },
  {
    name: "收起分镜列表后只剩序号列，详情跟着变宽",
    path: BOARD_PATH,
    api: API,
    ready: boardReady,
    act: async (page) => {
      const before = await box(shotList(page));
      await shotList(page).getByRole("button", { name: "收起分镜列表" }).click();
      await expect(shotList(page).getByRole("button", { name: "展开分镜列表" })).toBeVisible();
      const after = await box(shotList(page));
      expect(after.width).toBeLessThan(before.width / 3);
      await expect(page.getByRole("heading", { name: "引用" })).toBeVisible();
    },
  },
  {
    name: "展开对应原文与参考，长原文在中栏内滚动可达",
    path: BOARD_PATH,
    api: API,
    ready: boardReady,
    act: async (page) => {
      const trigger = page.getByRole("button", { name: "对应原文与参考" });
      await trigger.scrollIntoViewIfNeeded();
      await trigger.click();
      await expect(trigger).toHaveAttribute("aria-expanded", "true");
      const source = page.getByText(LONG_SOURCE);
      await source.scrollIntoViewIfNeeded();
      await expect(source).toBeInViewport();
    },
  },
  {
    name: "打开分镜备注弹层",
    path: BOARD_PATH,
    api: API,
    ready: boardReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await page.getByRole("button", { name: "备注", exact: true }).click();
      const popover = page.getByRole("dialog", { name: "备注" });
      await expect(popover.getByRole("textbox")).toBeFocused();
      await waitForEntrance(popover);
    },
    screenshot: { name: "shot-detail-notes", target: (page) => page.getByRole("dialog", { name: "备注" }) },
  },
  {
    name: "打开分镜图的版本历史并预览旧版本",
    path: BOARD_PATH,
    api: API,
    ready: boardReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      const trigger = page.getByRole("button", { name: "版本" }).first();
      await trigger.scrollIntoViewIfNeeded();
      await trigger.click();
      const popover = page.getByRole("dialog", { name: "历史版本" });
      await popover.getByRole("button", { name: "v1" }).click();
      await expect(popover.getByRole("img", { name: "版本 v1 预览" })).toBeVisible();
      await expect(popover.getByRole("button", { name: "切换到此版本" })).toBeEnabled();
      await waitForEntrance(popover);
      const rect = await box(popover);
      const { width, height } = viewport(page);
      expect(rect.y + rect.height).toBeLessThanOrEqual(height);
      expect(rect.x + rect.width).toBeLessThanOrEqual(width);
    },
    screenshot: { name: "shot-detail-versions", target: (page) => page.getByRole("dialog", { name: "历史版本" }) },
  },
  {
    name: "用键盘把第一个分镜移到第二个之后",
    path: BOARD_PATH,
    api: API,
    ready: boardReady,
    act: async (page) => {
      const move = page.waitForRequest(
        (request) => request.method() === "POST" && request.url().endsWith("/script-items/E1S01/move"),
      );
      await shotList(page).getByRole("button", { name: "调整 S01 的顺序" }).focus();
      await page.keyboard.press("Space");
      await expect(page.getByText(/已拿起「S01」/)).toBeAttached();
      await page.keyboard.press("ArrowDown");
      await expect(page.getByText(/「S01」移到第 2 项/)).toBeAttached();
      await page.keyboard.press("Space");
      expect((await move).postDataJSON()).toEqual({ script_file: "episode_1.json", after_id: "E1S02" });
    },
  },
]);
