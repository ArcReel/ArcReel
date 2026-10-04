import { box, waitForEntrance, clearAgentOverlay } from "../support/region-helpers.ts";
import type { Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { FIXED_NOW, recorded } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 集页「脚本规划」tab：没有脚本的集的起步态（起步区、本集原文与导览）、重新规划对话框与内容确认页。
const EPISODE_PATH = "/app/projects/demo/episodes/1";
const SOURCE_PATH = "GET /api/v1/projects/demo/source/episode_1.txt";
const TASKS_PATH = "GET /api/v1/tasks?page_size=200&project_name=demo";


interface RecordedProject {
  project: { episodes: Record<string, unknown>[] } & Record<string, unknown>;
  scripts: Record<string, unknown>;
}
interface RecordedPlan {
  status: {
    content: Record<string, unknown>;
    artifacts: Record<string, unknown>;
    operations: Record<string, unknown>;
  } & Record<string, unknown>;
  steps: ({ id: string } & Record<string, unknown>)[];
}

const project = recorded<RecordedProject>("project-demo.json");
const plan = recorded<RecordedPlan>("project-demo-workflow-plan.json");

// 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在录制的项目数据上。
const BASE: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

const BEATS = [
  "巡夜人沈砚在城墙下拾到一枚刻着旧年号的铜牌，铜牌背面有一道新鲜的划痕",
  "他把铜牌交给守备，守备却当着众人的面把它扔进了护城河",
  "当夜沈砚潜回河边，在淤泥里摸到的不止一枚铜牌",
  "茶楼说书人讲起十年前的旧案，提到的名字与铜牌上的一模一样",
  "沈砚去旧档房翻找卷宗，发现那一年的册页被整本撕走",
  "档房老吏拦住他，低声说起当年同样追查此事的另一个巡夜人",
  "那个巡夜人如今在城外开一间渡口茶棚，对旧事闭口不谈",
  "沈砚第三次登门时，茶棚已经人去楼空，桌上留着半壶温茶",
  "守备派人传话，要沈砚即日起调往北门，不得再碰旧案",
  "临走前他在铜牌的划痕里看出一个地名",
];
const HOOK = "渡口茶棚的灶膛里压着一封没烧完的信，落款是守备的私印——十年前的旧案并没有结束，而写信的人知道沈砚会来。";
const SOURCE_TEXT = Array.from(
  { length: 18 },
  (_, i) =>
    `第 ${i + 1} 段。夜色压低了城墙的轮廓，巡夜人提着灯从箭楼下走过，风把旗子吹得猎猎作响。沈砚停在拐角，听见护城河那边传来一声闷响，像是什么沉甸甸的东西落了水。他没有回头，只把灯往袖子里收了收，沿着墙根慢慢往河边走去。`,
).join("\n\n");

/** 没有脚本规划也没有正式脚本的集：页面进入「脚本规划」tab 的起步态。 */
function scriptlessProject(episode: Record<string, unknown>): ApiOverrides[string] {
  const { script_file: _scriptFile, script_status: _scriptStatus, ...recordedEpisode } = project.project.episodes[0];
  return {
    status: 200,
    body: {
      ...project,
      project: {
        ...project.project,
        episodes: [{ ...recordedEpisode, status: "draft", ...episode }],
      },
      scripts: {},
    },
  };
}

// 制作状态：本集已有集原文、还没有规划与正式脚本，下一步是规划脚本。
const PLANNING_NEXT = { type: "prepare_script_plan", args: { episode_id: 1 }, requested_ids: [], requires_confirmation: false, reason: "next" };
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

// 切自整本源文的集：长原文、十条节拍与长尾钩子。
const CUT_EPISODE: ApiOverrides = {
  ...BASE,
  "GET /api/v1/projects/demo": scriptlessProject({
    title: "第一集：夜色压低了城墙的轮廓，巡夜人提着灯从箭楼下走过",
    source_origin: "whole_source",
    source_range: { source_file: "source/长夜巡城.txt", start: 12840, end: 16220 },
    outline: { story_beats: BEATS },
    hook: HOOK,
  }),
  "POST /api/v1/projects/demo/workflow-plan": SCRIPTLESS_PLAN,
  [SOURCE_PATH]: { status: 200, body: SOURCE_TEXT },
};

// 自带原文的集：原文就地编辑。原文取三段，编辑区连同提示条能整块截进视口。
const OWN_TEXT = SOURCE_TEXT.split("\n\n").slice(0, 3).join("\n\n");
const OWN_EPISODE: ApiOverrides = {
  ...CUT_EPISODE,
  "GET /api/v1/projects/demo": scriptlessProject({ source_origin: "own", outline: { story_beats: BEATS.slice(0, 4) } }),
  [SOURCE_PATH]: { status: 200, body: OWN_TEXT },
};

// 本集的脚本规划任务正在跑：起步区换成状态说明。
const PLANNING_RUNNING: ApiOverrides = {
  ...CUT_EPISODE,
  [TASKS_PATH]: {
    status: 200,
    body: {
      items: [
        {
          task_id: "task-script-plan-1",
          project_name: "demo",
          task_type: "text_script_plan",
          media_type: "text",
          resource_id: "episode-1",
          resource_type: null,
          script_file: null,
          payload: {},
          status: "running",
          result: null,
          error_message: null,
          cancelled_by: null,
          provider_id: null,
          provider_job_id: null,
          source: "webui",
          queued_at: FIXED_NOW,
          started_at: FIXED_NOW,
          finished_at: null,
          updated_at: FIXED_NOW,
        },
      ],
      total: 1,
      page: 1,
      page_size: 200,
    },
  },
};

const NO_PLAN = {
  episode: 1,
  content_mode: "narration",
  fingerprint: null,
  confirmed_at: null,
  quarantine: null,
  supported_durations: null,
  duration_tiers: null,
  episode_target_duration: null,
  script_overwrite: null,
};

// 录制的集已有正式脚本、还没有规划：「AI 规划脚本」打开的是重新规划对话框。录制的制作状态里集原文缺失，
// 这里补成规划准入成立。
const SCRIPTED_NO_PLAN: ApiOverrides = {
  ...BASE,
  "POST /api/v1/projects/demo/workflow-plan": {
    status: 200,
    body: {
      ...plan,
      status: {
        ...plan.status,
        content: { ...plan.status.content, episode_source: "present" },
        operations: { ...plan.status.operations, prepare_script_plan: { state: "admitted" } },
      },
    },
  },
  "GET /api/v1/projects/demo/episodes/1/script-review": {
    status: 200,
    body: { ...NO_PLAN, status: "no_script_plan", content: null },
  },
};

// 内容确认页压力：二十四条长原文分镜，视频模型给出 4/6/8 秒三档。
const REVIEW_STRESS: ApiOverrides = {
  ...BASE,
  "GET /api/v1/projects/demo/video-capabilities": {
    status: 200,
    body: {
      provider_id: "gemini-aistudio",
      model: "veo-3.1-generate-preview",
      supported_durations: [4, 6, 8],
      max_duration: 8,
      max_reference_images: 3,
      first_frame: true,
      last_frame: true,
      source: "registry",
      voice_consistency: "none",
      duration_constraints: { resolution: null, uses_reference_images: false, allowed: [4, 6, 8], planning: [4, 6, 8], excluded: {} },
    },
  },
  "GET /api/v1/projects/demo/episodes/1/script-review": {
    status: 200,
    body: {
      ...NO_PLAN,
      status: "pending_review",
      fingerprint: "fp-stress",
      supported_durations: [4, 6, 8],
      episode_target_duration: 120,
      content: {
        segments: Array.from({ length: 24 }, (_, i) => ({
          segment_id: `E1S${String(i + 1).padStart(2, "0")}`,
          novel_text: `${BEATS[i % BEATS.length]}。${SOURCE_TEXT.slice(0, 120)}`,
          duration_seconds: [4, 6, 8][i % 3],
          segment_break: i % 6 === 5,
          characters_in_segment: ["沈砚", "守备"].slice(0, (i % 2) + 1),
          scenes: [],
          props: [],
        })),
      },
    },
  },
};

// 起步态在视图宽 860px 以上把导览放到右侧栏。
const GUIDE_RAIL_MIN_WIDTH = 860;

const viewTabs = (page: Page) => page.getByRole("tablist", { name: "集视图" });
const planPanel = (page: Page) => page.getByRole("tabpanel", { name: "脚本规划" });
const starterTitle = (page: Page) => page.getByRole("heading", { name: "这一集还没有脚本" });
const instructions = (page: Page) => page.getByRole("textbox", { name: "附加指令（可选）" });
const sourceRegion = (page: Page) => page.getByRole("region", { name: "本集原文" });
const guideRail = (page: Page) => page.getByRole("complementary", { name: "本集导览" });
const guideToggle = (page: Page) => page.getByRole("button", { name: /本集导览/ });
const progress = (page: Page) => page.getByTestId("workflow-panel");
const progressPopover = (page: Page) => page.getByRole("dialog", { name: "制作进度" });



/** 弹层带入场动画，量尺寸、跑 axe 之前等它停下；进行中的转圈等循环动画不等。 */

/** 紧凑档 Agent 面板盖在画布右侧：先收起再操作右侧的入口。 */

async function starterReady(page: Page) {
  await starterTitle(page).waitFor();
  await progress(page).waitFor();
}

async function cutSourceReady(page: Page) {
  await starterReady(page);
  await sourceRegion(page).getByText("第 18 段。", { exact: false }).waitFor();
}

/** 起步态的视图宽：导览放右栏还是原文上方由它决定。 */
async function railLayout(page: Page) {
  return (await box(planPanel(page))).width >= GUIDE_RAIL_MIN_WIDTH;
}

defineRegionScenarios("脚本规划起步态", [
  {
    name: "切自整本源文的集：起步区在上、原文只读，导览按视图宽放右栏或原文上方",
    path: EPISODE_PATH,
    api: CUT_EPISODE,
    ready: cutSourceReady,
    act: async (page) => {
      // 紧凑档 Agent 面板盖住画布右侧，收起后才看得到完整的起步态
      await clearAgentOverlay(page);
      await expect(viewTabs(page).getByRole("tab", { name: "脚本规划" })).toHaveAttribute("aria-selected", "true");
      await expect(viewTabs(page).getByRole("tab", { name: "原文" })).toHaveCount(0);
      const source = sourceRegion(page);
      await expect(source.getByRole("textbox")).toHaveCount(0);
      await expect(source.getByRole("link", { name: "去分集" })).toBeVisible();
      // 起步区在原文之上
      expect((await box(starterTitle(page))).y).toBeLessThan((await box(source)).y);
      if (await railLayout(page)) {
        await expect(guideRail(page)).toBeVisible();
        await expect(guideToggle(page)).toBeHidden();
        const rail = await box(guideRail(page));
        expect(rail.width).toBeGreaterThanOrEqual(260);
        expect(rail.width).toBeLessThanOrEqual(320);
      } else {
        await expect(guideRail(page)).toBeHidden();
        await expect(guideToggle(page)).toHaveAttribute("aria-expanded", "true");
        expect((await box(guideToggle(page))).y).toBeLessThan((await box(source)).y);
      }
    },
    screenshot: { name: "script-plan-start", target: planPanel },
  },
  {
    name: "起步态滚到底：右栏导览固定在视野里，窄视图的导览可以收起",
    path: EPISODE_PATH,
    api: CUT_EPISODE,
    ready: cutSourceReady,
    act: async (page) => {
      if (await railLayout(page)) {
        await sourceRegion(page).getByText("第 18 段。", { exact: false }).scrollIntoViewIfNeeded();
        const panel = await box(planPanel(page));
        const rail = await box(guideRail(page));
        expect(rail.y).toBeGreaterThanOrEqual(panel.y);
        expect(rail.y).toBeLessThan(panel.y + panel.height);
      } else {
        await guideToggle(page).click();
        await expect(guideToggle(page)).toHaveAttribute("aria-expanded", "false");
        await expect(page.getByText(BEATS[0])).toBeHidden();
      }
    },
  },
  {
    name: "分镜 tab 置灰，悬停说明脚本生成后可用",
    path: EPISODE_PATH,
    api: CUT_EPISODE,
    ready: cutSourceReady,
    act: async (page) => {
      const board = viewTabs(page).getByRole("tab", { name: "分镜" });
      await expect(board).toHaveAttribute("aria-disabled", "true");
      await expect(board).toHaveAccessibleDescription("脚本生成后可用");
      await board.hover({ force: true });
      const tooltip = page.locator("[data-slot=tooltip-content]");
      await expect(tooltip).toHaveText("脚本生成后可用");
      await waitForEntrance(tooltip);
    },
  },
  {
    name: "自带原文的集就地编辑原文，改动后出现未保存提示条",
    path: EPISODE_PATH,
    api: OWN_EPISODE,
    ready: async (page) => {
      await starterReady(page);
      await sourceRegion(page).getByRole("textbox", { name: "本集原文" }).waitFor();
    },
    act: async (page) => {
      await clearAgentOverlay(page);
      const source = sourceRegion(page);
      const editor = source.getByRole("textbox", { name: "本集原文" });
      await expect(editor).toHaveValue(OWN_TEXT);
      await expect(source.getByRole("link", { name: "去分集" })).toHaveCount(0);
      await editor.press("ControlOrMeta+End");
      await editor.pressSequentially("沈砚回到城墙下。");
      await expect(source.getByRole("button", { name: "保存" })).toBeVisible();
      await expect(source.getByRole("button", { name: "放弃修改" })).toBeVisible();
    },
    screenshot: { name: "script-plan-start-editing", target: sourceRegion },
  },
  {
    name: "制作进度的下一步跳到起步区并聚焦附加指令",
    path: EPISODE_PATH,
    api: CUT_EPISODE,
    ready: cutSourceReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await progress(page).click();
      const popover = progressPopover(page);
      await waitForEntrance(popover);
      await expect(popover.getByRole("textbox")).toHaveCount(0);
      await popover.getByTestId("workflow-next-step").getByRole("button", { name: "去规划脚本" }).click();
      await expect(popover).toBeHidden();
      await expect(instructions(page)).toBeFocused();
      await expect(instructions(page)).toBeInViewport();
    },
  },
  {
    name: "本集正在规划时起步区换成状态说明",
    path: EPISODE_PATH,
    api: PLANNING_RUNNING,
    ready: cutSourceReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await expect(planPanel(page).getByRole("status").filter({ hasText: "AI 正在规划脚本。" })).toBeVisible();
      await expect(instructions(page)).toHaveCount(0);
      await expect(page.getByRole("button", { name: "AI 规划", exact: true })).toHaveCount(0);
    },
    screenshot: { name: "script-plan-start-running", target: planPanel },
  },
]);

const replanDialog = (page: Page) => page.getByRole("dialog", { name: "AI 规划脚本" });

defineRegionScenarios("重新规划与内容确认", [
  {
    name: "已有正式脚本的集：「AI 规划脚本」打开对话框，说明确认后才替换正式脚本",
    path: `${EPISODE_PATH}?view=plan`,
    api: SCRIPTED_NO_PLAN,
    ready: async (page) => {
      await planPanel(page).getByText("暂无脚本规划结果").waitFor();
    },
    act: async (page) => {
      await expect(starterTitle(page)).toHaveCount(0);
      await clearAgentOverlay(page);
      await planPanel(page).getByRole("button", { name: "AI 规划脚本" }).click();
      const dialog = replanDialog(page);
      await waitForEntrance(dialog);
      await expect(dialog.getByText(/确认后才会替换/)).toBeVisible();
      await expect(dialog.getByRole("button", { name: "交给 Agent" })).toBeVisible();
      await expect(dialog.getByRole("button", { name: "AI 规划" })).toBeVisible();
    },
    screenshot: { name: "script-plan-replan-dialog", target: replanDialog },
  },
  {
    name: "内容确认页：二十四条长原文分镜只在视图内滚动",
    path: `${EPISODE_PATH}?view=plan`,
    api: REVIEW_STRESS,
    ready: async (page) => {
      await planPanel(page).getByText("S24", { exact: true }).waitFor();
    },
    act: async (page) => {
      await clearAgentOverlay(page);
      await expect(planPanel(page).getByRole("combobox", { name: "S01 时长" })).toBeVisible();
    },
    screenshot: { name: "script-plan-review", target: planPanel },
  },
  {
    name: "内容确认页展开分镜时长下拉",
    path: `${EPISODE_PATH}?view=plan`,
    api: REVIEW_STRESS,
    ready: async (page) => {
      await planPanel(page).getByText("S24", { exact: true }).waitFor();
    },
    act: async (page) => {
      await clearAgentOverlay(page);
      await planPanel(page).getByRole("combobox", { name: "S01 时长" }).click();
      const listbox = page.getByRole("listbox");
      await expect(listbox.getByRole("option")).toHaveText(["4 秒", "6 秒", "8 秒"]);
      await waitForEntrance(listbox);
    },
  },
]);
