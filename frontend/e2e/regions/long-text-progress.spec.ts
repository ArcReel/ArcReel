import { recordedDemoEpisode, workspaceOverride } from "../support/workspace-override.ts";
import type { Page } from "@playwright/test";
import { clearAgentOverlay, waitForEntrance } from "../support/region-helpers.ts";
import { FIXED_NOW, recorded } from "../support/recorded.ts";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

const EPISODE_PATH = "/app/projects/demo/episodes/1";
const EPISODES_PATH = "/app/projects/demo/episodes";
const USAGE_PATH = "/app/settings?section=usage";

const STARTED_16S_AGO = "2026-01-01T07:59:44.000Z";
const LONG_WAIT_HINT = "长文本通常需要数十秒至数分钟，可先切换处理其他页面。";
const WAIT_TIMEOUT_COPY = "本地已停止等待，但供应商仍可能继续完成请求并计费。确认结果后再重试";

interface RecordedPlan {
  status: {
    content: Record<string, unknown>;
    artifacts: Record<string, unknown>;
    operations: Record<string, unknown>;
  } & Record<string, unknown>;
  steps: ({ id: string; tasks?: unknown[] } & Record<string, unknown>)[];
}

const plan = recorded<RecordedPlan>("project-demo-workflow-plan.json");

const BASE: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

const runningText = recorded<{ items: [{ task_id: string; progress: { phase: "waiting"; model: string; seq: number } }] }>(
  "running-text-task.json",
).items[0];

function makeTextTask(overrides: Record<string, unknown>) {
  const { progress, ...row } = runningText;
  return {
    ...row,
    task_id: "task-text-1",
    project_name: "demo",
    provider_id: "anthropic",
    model: progress.model,
    queued_at: STARTED_16S_AGO,
    started_at: STARTED_16S_AGO,
    ...overrides,
  };
}

function scriptlessProject(episode: Record<string, unknown>): ApiOverrides {
  return {
    ...workspaceOverride({
      episodes: [{ ...recordedDemoEpisode(), script_file: "", script_status: "none", status: "draft", ...episode }],
    }),
    "GET /api/v1/projects/demo/scripts/episode_1.json": { status: 404, body: { detail: "Script not found" } },
  };
}

const PLANNING_NEXT = {
  type: "prepare_script_plan",
  args: { episode_id: 1 },
  requested_ids: [],
  requires_confirmation: false,
  reason: "next",
};

const SCRIPTLESS_PLAN: ApiOverrides[string] = {
  status: 200,
  body: {
    ...plan,
    status: {
      ...plan.status,
      content: { ...plan.status.content, episode_source: "present", formal_script: "absent", script_item_count: null },
      artifacts: { ...plan.status.artifacts, script: { state: "missing", path: null } },
      operations: { ...plan.status.operations, prepare_script_plan: { state: "admitted" } },
      next_action: PLANNING_NEXT,
    },
    next_action: PLANNING_NEXT,
    steps: plan.steps.map((step) => {
      if (step.id === "script_plan_content") return { ...step, state: "ready", action: PLANNING_NEXT };
      if (step.id === "final_script") return { ...step, state: "pending", action: null, artifacts: { state: "missing", path: null } };
      return step;
    }),
  },
};

function sseFrame(event: string, data: Record<string, unknown>): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

async function deliverTaskProgressOnce(
  page: Page,
  frames: string,
  waitBeforeSend: () => Promise<void>,
  waitAfterSend: () => Promise<void>,
) {
  let delivered = false;
  let releaseStream!: () => void;
  const streamGate = new Promise<void>((resolve) => {
    releaseStream = resolve;
  });
  await page.route("**/api/v1/projects/demo/events/stream", async (route) => {
    if (delivered) {
      await route.fulfill({ status: 404, json: { detail: "只推送一次" } });
      return;
    }
    delivered = true;
    await streamGate;
    await route.fulfill({ status: 200, body: frames, contentType: "text/event-stream" });
  });
  await page.reload();
  await waitBeforeSend();
  releaseStream();
  await waitAfterSend();
}

const PROMPT_AUTHORING_TASK = makeTextTask({
  task_id: "task-prompt-1",
  task_type: "text_episode_script",
  resource_id: "episode-1",
  progress: { ...runningText.progress, task_id: "task-prompt-1" },
});

const PROMPT_AUTHORING_RUNNING: ApiOverrides = {
  ...BASE,
  "POST /api/v1/projects/demo/workflow-plan": {
    status: 200,
    body: {
      ...plan,
      steps: plan.steps.map((step) =>
        step.id === "final_script"
          ? {
              ...step,
              state: "running",
              tasks: [
                {
                  task_id: PROMPT_AUTHORING_TASK.task_id,
                  task_type: "text_episode_script",
                  unit_id: "episode-1",
                  status: "running",
                },
              ],
            }
          : step,
      ),
    },
  },
  "GET /api/v1/tasks/active?project_name=demo": {
    status: 200,
    body: { items: [PROMPT_AUTHORING_TASK], truncated: false },
  },
  "GET /api/v1/tasks?page_size=200&project_name=demo": {
    status: 200,
    body: { items: [PROMPT_AUTHORING_TASK], total: 1, page: 1, page_size: 200 },
  },
  "GET /api/v1/tasks/stats?project_name=demo": {
    status: 200,
    body: { stats: { queued: 0, running: 1, succeeded: 0, failed: 0, cancelled: 0, total: 1 } },
  },
};

const SCRIPT_PLAN_TASK = makeTextTask({
  task_id: "task-script-plan-live",
  task_type: "text_drama_script_plan",
  resource_id: "episode-1",
  progress: { ...runningText.progress, task_id: "task-script-plan-live" },
});

const SCRIPT_PLAN_RUNNING: ApiOverrides = {
  ...BASE,
  ...scriptlessProject({ source_origin: "own" }),
  "POST /api/v1/projects/demo/workflow-plan": SCRIPTLESS_PLAN,
  "GET /api/v1/projects/demo/source/episode_1.txt": { status: 200, body: "夜色压低了城墙的轮廓。" },
  "GET /api/v1/tasks/active?project_name=demo": {
    status: 200,
    body: { items: [SCRIPT_PLAN_TASK], truncated: false },
  },
  "GET /api/v1/tasks?page_size=200&project_name=demo": {
    status: 200,
    body: { items: [SCRIPT_PLAN_TASK], total: 1, page: 1, page_size: 200 },
  },
};

const SCRIPT_PLAN_TIMEOUT: ApiOverrides = {
  ...BASE,
  ...scriptlessProject({ source_origin: "own" }),
  "POST /api/v1/projects/demo/workflow-plan": SCRIPTLESS_PLAN,
  "GET /api/v1/projects/demo/source/episode_1.txt": { status: 200, body: "夜色压低了城墙的轮廓。" },
  "GET /api/v1/tasks/active?project_name=demo": { status: 200, body: { items: [], truncated: false } },
  "GET /api/v1/tasks?page_size=200&project_name=demo": {
    status: 200,
    body: {
      items: [
        makeTextTask({
          task_id: "task-script-plan-timeout",
          task_type: "text_drama_script_plan",
          status: "failed",
          error_code: "text_wait_timeout",
          error_message: null,
          finished_at: FIXED_NOW,
        }),
      ],
      total: 1,
      page: 1,
      page_size: 200,
    },
  },
};

const EXACT_15000_SOURCE = "夜".repeat(15_000);
const LONG_SOURCE_EPISODE: ApiOverrides = {
  ...BASE,
  ...scriptlessProject({ source_origin: "own" }),
  "POST /api/v1/projects/demo/workflow-plan": SCRIPTLESS_PLAN,
  "GET /api/v1/projects/demo/source/episode_1.txt": { status: 200, body: EXACT_15000_SOURCE },
  "PUT /api/v1/projects/demo/episodes/1/source": {
    status: 200,
    body: recorded("episode-source-save.json"),
  },
  "POST /api/v1/projects/demo/episodes/1/script-plan": {
    status: 200,
    body: recorded("episode-script-plan.json"),
  },
};

const episodesView = recorded<{ episodes: Record<string, unknown>[] }>("project-demo-episodes-view.json");
const EPISODES_VIEW_WITH_SOURCE = {
  ...episodesView,
  units: 1200,
  cut_units: 400,
  files: [
    {
      source_file: "source/book.txt",
      name: "book.txt",
      original_filename: "book.txt",
      missing: false,
      changed_outside: false,
      length: 1200,
      units: 1200,
      cut_units: 400,
      segments: [
        {
          kind: "episode",
          start: 0,
          end: 400,
          text: "夜色压低了城墙的轮廓。",
          episode: 1,
          gap: false,
          units: 400,
          continued: false,
          continues: false,
        },
        {
          kind: "unsplit",
          start: 400,
          end: 1200,
          text: "渡口茶棚的灯影在雨夜里微微晃动。",
          episode: null,
          gap: false,
          units: 800,
          continued: false,
          continues: false,
        },
      ],
      source_kind: null,
    },
  ],
  episodes: [
    {
      ...episodesView.episodes[0],
      origin: "whole_source",
      placed: true,
      source_file: "source/book.txt",
      end_file: "source/book.txt",
      units: 400,
      spoken_seconds: 100,
      first_sentence: "夜色压低了城墙的轮廓",
      last_sentence: "夜色压低了城墙的轮廓",
      source_kind: null,
    },
  ],
};

const EPISODE_PLAN_TASK = makeTextTask({
  task_id: "task-ep-plan-1",
  task_type: "text_episode_plan",
  resource_id: "episode-planning",
  progress: { task_id: "task-ep-plan-1", seq: 3, phase: "retrying", error: "429", attempt: 2, max_attempts: 3 },
});

const EPISODE_PLAN_RUNNING: ApiOverrides = {
  ...BASE,
  "GET /api/v1/projects/demo/episodes-view": { status: 200, body: EPISODES_VIEW_WITH_SOURCE },
  "GET /api/v1/tasks/active?project_name=demo": {
    status: 200,
    body: { items: [EPISODE_PLAN_TASK], truncated: false },
  },
  "GET /api/v1/tasks?page_size=200&project_name=demo": {
    status: 200,
    body: { items: [EPISODE_PLAN_TASK], total: 1, page: 1, page_size: 200 },
  },
};

const EPISODE_PLAN_TIMEOUT: ApiOverrides = {
  ...BASE,
  "GET /api/v1/projects/demo/episodes-view": { status: 200, body: EPISODES_VIEW_WITH_SOURCE },
  "GET /api/v1/tasks/active?project_name=demo": { status: 200, body: { items: [], truncated: false } },
  "GET /api/v1/tasks?page_size=200&project_name=demo": {
    status: 200,
    body: {
      items: [
        makeTextTask({
          task_id: "task-ep-plan-timeout",
          task_type: "text_episode_plan",
          resource_id: "episode-planning",
          status: "failed",
          error_code: "text_wait_timeout",
          error_message: null,
          finished_at: FIXED_NOW,
        }),
      ],
      total: 1,
      page: 1,
      page_size: 200,
    },
  },
};

const GLOBAL_ACTIVE_TEXT_TASKS: ApiOverrides = {
  "GET /api/v1/tasks/active": {
    status: 200,
    body: {
      items: [
        makeTextTask({
          task_id: "task-global-1",
          project_name: "demo",
          task_type: "text_episode_plan",
          resource_id: "episode-planning",
          model: "claude-sonnet-4",
          progress: { task_id: "task-global-1", seq: 2, phase: "receiving", thinking: false, characters: 920 },
        }),
        makeTextTask({
          task_id: "task-global-2",
          project_name: "other-project",
          task_type: "text_drama_script_plan",
          resource_id: "episode-1",
          provider_id: "openai",
          model: "gpt-5",
          progress: { task_id: "task-global-2", seq: 1, phase: "receiving", thinking: true, characters: 0 },
        }),
      ],
      truncated: false,
    },
  },
};

defineRegionScenarios("长文本等待反馈与超长原文引导", [
  {
    name: "提示词编写、顶栏用量浮窗与制作进度任务条接收动态 task_progress 流并显示超 15 秒提示",
    viewports: "both",
    a11y: true,
    path: EPISODE_PATH,
    api: PROMPT_AUTHORING_RUNNING,
    ready: async (page) => {
      await page.getByRole("button", { name: "编写提示词" }).waitFor();
      await page.getByText("正在等待 claude-sonnet-4 响应").waitFor();
    },
    act: async (page) => {
      await clearAgentOverlay(page);
      await expect(page.getByRole("button", { name: "编写提示词" })).toBeDisabled();
      await expect(page.getByText(LONG_WAIT_HINT)).toBeVisible();

      await deliverTaskProgressOnce(
        page,
        sseFrame("task_progress", {
          task_id: PROMPT_AUTHORING_TASK.task_id,
          seq: 2,
          phase: "receiving",
          thinking: false,
          characters: 640,
        }),
        async () => {
          await page.getByText("正在等待 claude-sonnet-4 响应").waitFor();
        },
        async () => {
          await page.getByText("正在接收生成内容 · 已接收约 640 字").waitFor();
        },
      );
      await clearAgentOverlay(page);
      await expect(page.getByText("正在接收生成内容 · 已接收约 640 字")).toBeVisible();
      await expect(page.getByText(LONG_WAIT_HINT)).toBeVisible();

      await page.getByRole("button", { name: /^使用记录 · 参考费用/ }).click();
      const usagePopover = page.getByRole("dialog", { name: "使用记录" });
      await waitForEntrance(usagePopover);
      await expect(usagePopover.getByText("正在接收生成内容 · 已接收约 640 字")).toBeVisible();
      await expect(usagePopover.getByRole("status").filter({ hasText: /^正在接收生成内容…$/ })).toBeVisible();
      await expect(usagePopover.getByText(LONG_WAIT_HINT)).toBeVisible();
      await usagePopover.getByRole("button", { name: "关闭使用记录" }).click();
      await expect(usagePopover).toBeHidden();

      await page.getByTestId("workflow-panel").click();
      const workflowPopover = page.getByRole("dialog", { name: "制作进度" });
      await waitForEntrance(workflowPopover);
      await expect(workflowPopover.getByText("正在接收生成内容 · 已接收约 640 字")).toBeVisible();
      await expect(workflowPopover.getByText(LONG_WAIT_HINT)).toBeVisible();
    },
  },
  {
    name: "脚本规划启动区接收动态 task_progress 流并显示已耗时与超 15 秒提示",
    path: EPISODE_PATH,
    api: SCRIPT_PLAN_RUNNING,
    ready: async (page) => {
      await page.getByRole("heading", { name: "这一集还没有脚本" }).waitFor();
      await page.getByText("正在等待 claude-sonnet-4 响应").waitFor();
    },
    act: async (page) => {
      await clearAgentOverlay(page);
      await deliverTaskProgressOnce(
        page,
        sseFrame("task_progress", {
          task_id: SCRIPT_PLAN_TASK.task_id,
          seq: 2,
          phase: "receiving",
          thinking: false,
          characters: 1280,
        }),
        async () => {
          await page.getByText("正在等待 claude-sonnet-4 响应").waitFor();
        },
        async () => {
          await page.getByText("正在接收生成内容 · 已接收约 1280 字").waitFor();
        },
      );
      await clearAgentOverlay(page);
      const panel = page.getByRole("tabpanel", { name: "脚本规划" });
      await expect(panel.getByRole("status")).toHaveText("正在接收生成内容…");
      await expect(panel).toContainText("AI 正在规划脚本。");
      await expect(panel).toContainText("正在接收生成内容 · 已接收约 1280 字");
      await expect(panel).toContainText(LONG_WAIT_HINT);
    },
  },
  {
    name: "脚本规划等待超时时明确显示诚实失败文案",
    path: EPISODE_PATH,
    api: SCRIPT_PLAN_TIMEOUT,
    ready: async (page) => {
      await page.getByRole("heading", { name: "这一集还没有脚本" }).waitFor();
      await page.getByRole("alert").filter({ hasText: WAIT_TIMEOUT_COPY }).waitFor();
    },
    act: async (page) => {
      await clearAgentOverlay(page);
      await expect(page.getByRole("alert")).toContainText(WAIT_TIMEOUT_COPY);
    },
  },
  {
    name: "单集超长原文引导在 15,000 字不提示，未保存编辑到 15,001 字非阻断提示且仍可提交",
    path: EPISODE_PATH,
    api: LONG_SOURCE_EPISODE,
    ready: async (page) => {
      await page.getByRole("heading", { name: "这一集还没有脚本" }).waitFor();
      await page.getByRole("textbox", { name: "本集原文" }).waitFor();
    },
    act: async (page) => {
      await clearAgentOverlay(page);
      const planPanel = page.getByRole("tabpanel", { name: "脚本规划" });
      await expect(planPanel.getByText(/超过 15,000 字/)).toHaveCount(0);

      await planPanel.getByRole("textbox", { name: "附加指令（可选）" }).fill("指".repeat(500));
      await expect(planPanel.getByText(/超过 15,000 字/)).toHaveCount(0);

      const sourceBox = planPanel.getByRole("textbox", { name: "本集原文" });
      await sourceBox.press("ControlOrMeta+End");
      await sourceBox.pressSequentially("色");

      await expect(planPanel.getByText(/本集原文约 15,001 字，超过 15,000 字/)).toBeVisible();
      await expect(planPanel.getByRole("link", { name: "去分集" })).toBeVisible();

      const planResponse = page.waitForResponse(
        (res) => res.request().method() === "POST" && res.url().endsWith("/api/v1/projects/demo/episodes/1/script-plan"),
      );
      const aiButton = planPanel.getByRole("button", { name: "AI 规划" });
      await expect(aiButton).toBeEnabled();
      await aiButton.click();
      const response = await planResponse;
      expect(response.ok()).toBe(true);
      expect(await response.finished()).toBeNull();
      await expect(planPanel.getByText("AI 规划脚本正在排队。", { exact: true })).toBeVisible();
    },
  },
  {
    name: "分集规划单窗等待态展示重试阶段与超 15 秒提示",
    path: EPISODES_PATH,
    api: EPISODE_PLAN_RUNNING,
    ready: async (page) => {
      await page.getByRole("button", { name: /AI 规划中/ }).waitFor();
    },
    act: async (page) => {
      await clearAgentOverlay(page);
      await page.getByRole("button", { name: /AI 规划中/ }).click();
      const popover = page.getByRole("dialog");
      await waitForEntrance(popover);
      await expect(popover.getByText("正在重试（第 2/3 次尝试）")).toBeVisible();
      await expect(popover.getByText(LONG_WAIT_HINT)).toBeVisible();
    },
  },
  {
    name: "分集规划触达等待上限失败时明确显示诚实超时文案",
    path: EPISODES_PATH,
    api: EPISODE_PLAN_TIMEOUT,
    ready: async (page) => {
      await page.getByRole("button", { name: "AI 规划剩余内容" }).waitFor();
    },
    act: async (page) => {
      await clearAgentOverlay(page);
      await page.getByRole("button", { name: "AI 规划剩余内容" }).click();
      const popover = page.getByRole("dialog");
      await waitForEntrance(popover);
      await expect(popover.getByRole("alert")).toContainText(WAIT_TIMEOUT_COPY);
    },
  },
  {
    name: "全局使用记录页从跨项目活跃快照展示多项目长文本进展与超 15 秒提示",
    path: USAGE_PATH,
    api: GLOBAL_ACTIVE_TEXT_TASKS,
    ready: async (page) => {
      await page.getByRole("heading", { name: "使用记录" }).waitFor();
      await page.getByText("正在接收生成内容 · 已接收约 920 字").waitFor();
    },
    act: async (page) => {
      await expect(page.getByText("demo")).toBeVisible();
      await expect(page.getByText("other-project")).toBeVisible();
      const receiving = page.getByText("正在接收生成内容 · 已接收约 920 字");
      await expect(receiving).toBeVisible();
      await expect(page.getByRole("status").filter({ hasText: /^正在接收生成内容…$/ })).toBeVisible();
      expect(await receiving.evaluate((label) => label.scrollWidth - label.clientWidth)).toBeLessThanOrEqual(1);
      await expect(page.getByText("模型思考中")).toBeVisible();
      await expect(page.getByText(LONG_WAIT_HINT).first()).toBeVisible();
    },
  },
]);
