import type { Page } from "@playwright/test";
import { waitForEntrance } from "../support/region-helpers.ts";
import { recorded } from "../support/recorded.ts";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 顶栏中间的状态条：集进度弹层（逐集清单）、项目层的下一步弹层，以及数据升级失败时的重试形态。
const OVERVIEW_PATH = "/app/projects/demo";

interface RecordedProject {
  project: { status: Record<string, unknown>; episodes: Record<string, unknown>[] } & Record<string, unknown>;
}
interface RecordedWorkflowStatus {
  next_action: Record<string, unknown>;
}

const recordedProject = recorded<RecordedProject>("project-demo.json");
const workflowStatus = recorded<RecordedWorkflowStatus>("project-demo-workflow-status.json");
const [firstEpisode] = recordedProject.project.episodes;

const EVENT_STREAM: ApiOverrides = {
  // 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在替换后的数据上。
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

function action(type: string, args: Record<string, unknown> = {}) {
  return { type, args, requested_ids: [], requires_confirmation: false, reason: "" };
}

function projectWith(status: Record<string, unknown>, episodes: Record<string, unknown>[]): ApiOverrides {
  return {
    "GET /api/v1/projects/demo": {
      status: 200,
      body: { ...recordedProject, project: { ...recordedProject.project, status: { ...recordedProject.project.status, ...status }, episodes } },
    },
  };
}

const EPISODE_COUNT = 30;
const EPISODE_TITLE = (i: number) => `第 ${i} 集：夜色压低了城墙的轮廓，巡夜人提着灯从箭楼下走过`;
const STATUSES = ["completed", "in_production", "scripted", "draft"];

// 30 集长标题：逐集清单在弹层里滚动，最后一集可达。
const MANY_EPISODES: ApiOverrides = {
  ...EVENT_STREAM,
  ...projectWith(
    { episodes_summary: { total: EPISODE_COUNT, scripted: 20, in_production: 6, completed: 8 } },
    Array.from({ length: EPISODE_COUNT }, (_, i) => ({
      ...firstEpisode,
      episode: i + 1,
      title: EPISODE_TITLE(i + 1),
      status: STATUSES[i % STATUSES.length],
      script_file: `scripts/episode_${i + 1}.json`,
      ...(i % 5 === 1 ? { videos: { total: 4, available: 2, stale: 2 } } : {}),
    })),
  ),
  "GET /api/v1/projects/demo/workflow-status": {
    status: 200,
    body: { ...workflowStatus, next_action: action("generate_videos", { episode_id: 2 }) },
  },
  "GET /api/v1/projects/demo/workflow-status/episodes": {
    status: 200,
    body: {
      episodes: Array.from({ length: EPISODE_COUNT }, (_, i) => ({
        episode: i + 1,
        plan_stale: i % 7 === 3,
        next_action: action(i % 4 === 0 ? "none" : "generate_videos", { episode_id: i + 1 }),
      })),
    },
  },
};

// 尚未建集：下一步是 AI 分集规划，弹层里有附加要求输入框与「或者」的备选。
const PLAN_EPISODES: ApiOverrides = {
  ...EVENT_STREAM,
  ...projectWith({ episodes_summary: { total: 0, scripted: 0, in_production: 0, completed: 0 } }, []),
  "GET /api/v1/projects/demo/workflow-status": {
    status: 200,
    body: { ...workflowStatus, next_action: action("plan_episodes"), next_alternatives: [action("create_episode")] },
  },
};

const LONG_REASON =
  "迁移步骤 0014_split_episode_scripts 读取 projects/demo/scripts/episode_4_with_an_unusually_long_file_name_for_testing.json 时失败：JSON 第 812 行第 17 列缺少逗号，文件可能在上次保存时被截断。";

// 数据升级失败：整条换成重试形态，重试再失败时弹层自动展开原因。
const MIGRATION_FAILED: ApiOverrides = {
  ...EVENT_STREAM,
  ...projectWith({ needs_repair: true, repair_reason: LONG_REASON }, recordedProject.project.episodes),
  "POST /api/v1/projects/demo/migration/retry": {
    status: 422,
    body: { detail: "数据升级仍未完成", diagnostic: { reason: LONG_REASON, details: [] } },
  },
};

const popover = (page: Page) => page.locator('[data-slot="popover-content"]');

async function headerReady(page: Page) {
  await page.getByRole("button", { name: /^切换项目/ }).waitFor();
}

defineRegionScenarios("顶栏状态条", [
  {
    name: "30 集长标题时打开集进度，逐集清单在弹层里滚动",
    path: OVERVIEW_PATH,
    api: MANY_EPISODES,
    ready: async (page) => {
      await headerReady(page);
      await page.getByRole("button", { name: /继续第 2 集/ }).waitFor();
    },
    act: async (page) => {
      await page.getByRole("button", { name: /8\/30 集/ }).click();
      const rows = popover(page).getByRole("listitem");
      await expect(rows).toHaveCount(EPISODE_COUNT);
      const last = rows.last().getByRole("button");
      await last.scrollIntoViewIfNeeded();
      await expect(last).toBeInViewport({ ratio: 1 });
      await waitForEntrance(popover(page));
    },
    screenshot: { name: "project-status-bar-episodes", target: popover },
  },
  {
    name: "打开 AI 分集规划的下一步，附加要求输入框与备选都在弹层里",
    path: OVERVIEW_PATH,
    api: PLAN_EPISODES,
    ready: async (page) => {
      await headerReady(page);
      await page.getByRole("button", { name: /AI 分集规划/ }).waitFor();
    },
    act: async (page) => {
      await page.getByRole("button", { name: /AI 分集规划/ }).click();
      await popover(page).getByRole("textbox").fill("每集 90 秒，结尾留悬念");
      await expect(popover(page).getByRole("button", { name: "新建一集" })).toBeInViewport({ ratio: 1 });
      await waitForEntrance(popover(page));
    },
    screenshot: { name: "project-status-bar-next", target: popover },
  },
  {
    name: "数据升级重试失败后，弹层展开很长的失败原因",
    path: OVERVIEW_PATH,
    api: MIGRATION_FAILED,
    ready: async (page) => {
      await headerReady(page);
      await page.getByRole("button", { name: "重试" }).waitFor();
    },
    act: async (page) => {
      await page.getByRole("button", { name: "重试" }).click();
      await expect(popover(page).getByRole("button", { name: "交给 Agent 排查" })).toBeInViewport({ ratio: 1 });
      await waitForEntrance(popover(page));
    },
    screenshot: { name: "project-status-bar-migration", target: popover },
  },
]);
