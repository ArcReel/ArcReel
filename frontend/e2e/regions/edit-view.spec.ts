import { box, viewport, waitForEntrance, clearAgentOverlay } from "../support/region-helpers.ts";
import type { Locator, Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { FIXED_NOW, recorded } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 集页剪辑视图：播放器在左、详情栏在右，轨道横跨底部；整个视图不滚动，只有详情栏滚动。以及出片对话框。
const EDIT_PATH = "/app/projects/demo/episodes/1?view=edit";
// 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在录制的项目数据上。
const API: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};


interface RecordedProject {
  project: Record<string, unknown>;
}

const project = recorded<RecordedProject>("project-demo.json");

// 压力变体：多条长名称的剪辑时间线，36 个片段约 2 分半，旁白、长字幕、两段 BGM、40 多条问题与 30 个未使用的视频单元。
const TIMELINE_ID = "tl-00000006";
const unit = (index: number) => `E1S${String(index + 1).padStart(2, "0")}`;

const timelineNames = [
  "按脚本顺序",
  "初剪",
  "快节奏版：开场三秒内必须出现城墙与巡夜人的灯",
  "竖屏投放版",
  "导演剪辑版（加长）",
  "定稿：夜色压低了城墙的轮廓，巡夜人提着灯从箭楼下走过",
];
const timelines = timelineNames.map((name, index) => ({
  id: `tl-0000000${index + 1}`,
  name,
  episode: 1,
  revision: index + 1,
  clip_count: 36,
  created_at: FIXED_NOW,
  updated_at: FIXED_NOW,
  updated_by: { kind: index === 5 ? "arcreel_agent" : "creator", user_id: null },
  update_summary: "调整节奏",
  agent_turn: null,
}));

const DURATIONS = [3.5, 2, 4.5, 3, 5, 2.5];
let cursor = 0;
const clips = Array.from({ length: 36 }, (_, index) => {
  const duration = DURATIONS[index % DURATIONS.length];
  const start = cursor;
  cursor += duration;
  const missing = index === 7 || index === 21;
  return {
    id: `c${index + 1}`,
    unit_id: unit(index),
    status: missing ? "video_missing" : "ready",
    start,
    duration,
    video_version: missing ? null : 1,
    source_duration: missing ? null : duration + 1.5,
    trim: index % 4 === 0 ? { source_in: 0.5, source_out: duration + 0.5, basis_version: 1 } : null,
    source_volume: 1,
    hold: 0,
    carries_narration: index % 3 === 0,
    narration: index % 3 === 0 ? { start, end: start + duration + 1.2 } : null,
    reason:
      index === 0
        ? "开场保留巡夜人从箭楼下走过的完整动作，灯影扫过城墙时切到下一个镜头；这里不要截短，后面的旁白需要这一段的长度来铺垫夜色与风声。"
        : null,
    transition_to_next: index % 5 === 4 ? { type: "dissolve", duration: 0.5 } : null,
  };
});
const totalDuration = cursor;

const ISSUE_PARAMS = { basis_version: 1, current_version: 2 };
const readout = {
  timeline: { id: TIMELINE_ID, name: timelineNames[5], episode: 1 },
  revision: 6,
  latest_revision: 6,
  duration: totalDuration,
  clips,
  bgm: [
    { id: "b1", bgm_id: "bgm-0001", name: "雨夜·城墙下的风声（循环版）", start: 0, end: 60, source_in: 0, source_out: 60, volume: 0.3, fade_in: 2, fade_out: 3 },
    { id: "b2", bgm_id: "bgm-0002", name: "箭楼钟声", start: 60, end: totalDuration, source_in: 0, source_out: totalDuration - 60, volume: 0.25, fade_in: 1, fade_out: 4 },
  ],
  issues: [
    ...clips
      .filter((clip) => clip.trim)
      .map((clip) => ({
        code: "trim_ignored",
        severity: "info",
        applies_to: "all",
        clip_ids: [clip.id],
        unit_id: clip.unit_id,
        params: ISSUE_PARAMS,
      })),
    ...[7, 21].map((index) => ({
      code: "video_missing",
      severity: "warning",
      applies_to: "all",
      clip_ids: [clips[index].id],
      unit_id: clips[index].unit_id,
      params: {},
    })),
    ...Array.from({ length: 30 }, (_, i) => ({
      code: "unit_unused",
      severity: "info",
      applies_to: "all",
      clip_ids: [],
      unit_id: unit(36 + i),
      params: {},
    })),
  ],
};

const previewMedia = {
  timeline_id: TIMELINE_ID,
  revision: 6,
  narration: "without_narration",
  units: clips.map((clip, index) => ({
    unit_id: clip.unit_id,
    provider_audio: false,
    narration_audio: null,
    subtitles_follow_narration: false,
    subtitles: [
      {
        start: 0,
        duration: clip.duration,
        text: index % 2 === 0 ? "夜色压低了城墙的轮廓，巡夜人提着灯从箭楼下走过。" : "风把旗子吹得猎猎作响。",
      },
    ],
  })),
  bgm: [
    { bgm_id: "bgm-0001", path: "bgm/bgm-0001.mp3", gain: 1 },
    { bgm_id: "bgm-0002", path: "bgm/bgm-0002.mp3", gain: 1 },
  ],
};

function finalCut(status: "current" | "stale" | "missing", narration = "without_narration") {
  return {
    episode: 1,
    timeline_id: TIMELINE_ID,
    status,
    artifact_path: `renders/episode_1/${TIMELINE_ID}/final_cut.mp4`,
    version: status === "missing" ? null : 3,
    rendered_at: status === "missing" ? null : FIXED_NOW,
    narration,
    subtitles: "burned_subtitles",
    download_url: status === "missing" ? null : `/api/v1/files/demo/renders/episode_1/${TIMELINE_ID}/final_cut.mp4?v=3`,
  };
}

const NO_TASKS = { status: 200, body: { items: [], total: 0, page: 1, page_size: 20 } };
const TIMELINE_BASE = `/api/v1/projects/demo/edit-timelines/${TIMELINE_ID}`;
// 播放器装载当前片段的源视频，开始播放后再预载下一段；套件不回放媒体文件，按不存在处理。
const videoMissing = (index: number) => ({
  [`GET /api/v1/files/demo/videos/scene_${unit(index)}.mp4?v=1`]: { status: 404, body: { detail: "页面级套件不回放媒体" } },
});

const TIMELINE: ApiOverrides = {
  ...API,
  "GET /api/v1/projects/demo/edit-timelines?episode=1": { status: 200, body: { timelines } },
  [`GET ${TIMELINE_BASE}`]: { status: 200, body: readout },
  [`GET ${TIMELINE_BASE}/preview-media`]: { status: 200, body: previewMedia },
  ...videoMissing(0),
  ...videoMissing(1),
  "GET /api/v1/files/demo/bgm/bgm-0001.mp3": { status: 404, body: { detail: "页面级套件不回放媒体" } },
  "GET /api/v1/files/demo/bgm/bgm-0002.mp3": { status: 404, body: { detail: "页面级套件不回放媒体" } },
  // 出片对话框：成片过时，剪映草稿尚未生成；没有在途任务。
  [`GET ${TIMELINE_BASE}/final-cut?narration=without_narration&subtitles=burned_subtitles`]: {
    status: 200,
    body: finalCut("stale"),
  },
  [`GET ${TIMELINE_BASE}/jianying-draft?narration=without_narration`]: {
    status: 200,
    body: { ...finalCut("missing"), download_url: undefined, subtitles: undefined },
  },
  "GET /api/v1/tasks?project_name=demo&status=running&task_type=render_final_cut": NO_TASKS,
  "GET /api/v1/tasks?project_name=demo&status=queued&task_type=render_final_cut": NO_TASKS,
  "GET /api/v1/tasks?project_name=demo&status=running&task_type=render_jianying_draft": NO_TASKS,
  "GET /api/v1/tasks?project_name=demo&status=queued&task_type=render_jianying_draft": NO_TASKS,
};

// TTS 配音项目：出片对话框多出旁白版本，剪映草稿时最高。
const TTS_TIMELINE: ApiOverrides = {
  ...TIMELINE,
  "GET /api/v1/projects/demo": {
    status: 200,
    body: { ...project, project: { ...project.project, narration_delivery: "use_tts" } },
  },
  [`GET ${TIMELINE_BASE}/final-cut?narration=with_narration&subtitles=burned_subtitles`]: {
    status: 200,
    body: finalCut("current", "with_narration"),
  },
  [`GET ${TIMELINE_BASE}/jianying-draft?narration=with_narration`]: {
    status: 200,
    body: { ...finalCut("stale", "with_narration"), download_url: undefined, subtitles: undefined },
  },
};


const editView = (page: Page) => page.getByRole("tabpanel", { name: "剪辑", exact: true });
const timelineTabs = (page: Page) => page.getByRole("tablist", { name: "剪辑时间线" });
const tracks = (page: Page) => page.getByRole("group", { name: "时间线轨道，点击跳到对应时间" });
const details = (page: Page) => page.getByRole("region", { name: "片段详情与问题" });
const player = (page: Page) => page.getByRole("region", { name: "剪辑预览" });
const renderDialog = (page: Page) => page.getByRole("dialog", { name: /^出片 · / });



/** 弹层带入场动画，量尺寸、跑 axe 之前等它停下；进行中的转圈等循环动画不等。 */

/** 紧凑档 Agent 面板盖在画布右侧，详情栏与页头行尾的出片按钮在它底下：先收起再操作。 */

async function timelineReady(page: Page) {
  await editView(page).waitFor();
  await page.getByTestId("edit-clip-c36").waitFor({ state: "attached" });
}

async function openRenderDialog(page: Page) {
  await clearAgentOverlay(page);
  await page.getByRole("button", { name: "出片", exact: true }).click();
  await waitForEntrance(renderDialog(page));
  await expect(renderDialog(page).getByTestId("edit-render-artifact-status")).toBeVisible();
}

/** 主操作完整落在视口内。 */
async function expectInViewport(page: Page, target: Locator) {
  const rect = await box(target);
  const { width, height } = viewport(page);
  expect(rect.x).toBeGreaterThanOrEqual(0);
  expect(rect.y).toBeGreaterThanOrEqual(0);
  expect(rect.x + rect.width).toBeLessThanOrEqual(width);
  expect(rect.y + rect.height).toBeLessThanOrEqual(height);
}

defineRegionScenarios("剪辑视图", [
  {
    name: "本集还没有剪辑时间线时显示两个入口",
    path: EDIT_PATH,
    api: API,
    ready: async (page) => {
      await editView(page).waitFor();
      await page.getByRole("heading", { name: "这一集还没有剪辑时间线" }).waitFor();
    },
    act: async (page) => {
      await expect(page.getByRole("button", { name: "交给 Agent 剪辑" })).toBeVisible();
      await expect(page.getByRole("button", { name: "新建剪辑时间线" })).toBeVisible();
    },
  },
  {
    name: "播放器、详情栏与四条轨道同屏，只有详情栏滚动",
    path: EDIT_PATH,
    api: TIMELINE,
    ready: timelineReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      // 选中带长说明的片段，详情栏撑到超高
      await page.getByTestId("edit-clip-c1").click();
      await expect(page.getByTestId("edit-clip-inspector")).toContainText("开场保留巡夜人");
      await expect(timelineTabs(page).getByRole("tab", { name: timelineNames[5] })).toHaveAttribute("aria-selected", "true");

      // 四条轨道完整落在视口内，轨道组的底边不超出视图
      const { height } = viewport(page);
      const group = await box(tracks(page));
      expect(group.y + group.height).toBeLessThanOrEqual(height);
      const view = await box(editView(page));
      expect(group.y + group.height).toBeLessThanOrEqual(view.y + view.height);
      await expect(page.getByTestId("edit-bgm-b2")).toBeAttached();

      // 详情栏内容超高时自己滚动，播放器不被压没
      const scrollable = await details(page).evaluate((el) => el.scrollHeight > el.clientHeight);
      expect(scrollable).toBe(true);
      expect((await box(player(page))).height).toBeGreaterThan(0);
    },
    screenshot: { name: "edit-view", target: editView },
  },
  {
    name: "出片对话框：成片过时，以重新渲染为主动作",
    path: EDIT_PATH,
    api: TIMELINE,
    ready: timelineReady,
    act: async (page) => {
      await openRenderDialog(page);
      await expect(renderDialog(page)).toContainText("已落后于剪辑时间线");
      await expectInViewport(page, renderDialog(page).getByRole("button", { name: "重新渲染" }));
    },
    screenshot: { name: "edit-render-dialog", target: renderDialog },
  },
  {
    name: "出片对话框：TTS 配音项目的剪映草稿选项最多，主操作仍在视口内",
    path: EDIT_PATH,
    api: TTS_TIMELINE,
    ready: timelineReady,
    act: async (page) => {
      await openRenderDialog(page);
      // 原生单选框藏在卡片里，点卡片上的名称
      await renderDialog(page).getByText("剪映草稿", { exact: true }).click();
      await expect(renderDialog(page).getByRole("radiogroup", { name: "剪映版本" })).toBeVisible();
      await expect(renderDialog(page).getByRole("radiogroup", { name: "旁白版本" })).toBeVisible();
      await expectInViewport(page, renderDialog(page).getByRole("button", { name: "重新导出" }));
    },
  },
]);
