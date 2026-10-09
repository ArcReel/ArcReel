import type { Page } from "@playwright/test";
import { waitForEntrance } from "../support/region-helpers.ts";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";
import { idleSessionApi } from "../support/assistant-session.ts";

// Agent 面板输入区：Agent 提问时的问卷、待办进度行、排队消息托盘、会话历史与删除确认、斜杠命令菜单、
// 输入框里的图片附件。会话、提问与排队消息都是手写的压力数据：长标题、长选项、多条会话、多条排队消息。
const EPISODE_PATH = "/app/projects/demo/episodes/1";
const SESSIONS_PATH = "/api/v1/projects/demo/assistant/sessions";
const PANEL_WIDTH_KEY = "arcreel_assistant_panel_width";
const ASK_SESSION_ID = "session-ask";
const IDLE_SESSION_ID = "session-0";
const QUEUE_SESSION_ID = "session-queue";

// 160×96 的双色方格 PNG，缩略图里看得出是一张图。
const IMAGE_DATA =
  "iVBORw0KGgoAAAANSUhEUgAAAKAAAABgCAIAAAAVRe7OAAAA+UlEQVR42u3bQQ0AIAwDwKnihQI08EAVeidjYVxSA+29G3vdkox5SvJb3wAMGDBgwIABAwYMGDBgwIABAwYMGDBgwIABAwYMGDBgwIABAwYMGDBgwIABAwYMGDBgwIABAwYMGDBgwIABAwbcCtjQvfsCBgwYMGDAgAEDBgwYMGDAgAEDBgwYMGDAgAEDBgwYMGDAgAEDBgwYMGDAgAEDBgwYMGDAgAEDBgwYMGDAgAED7gVsaA9/wIABAwYMGDBgwIABAwYMGDBgwIABAwYMGDBgwIABAwYMGDBgwIABAwYMGDBgwIABAwYMGDBgwIABAwYMGDBgwM/0TQmWp5SVOq3vAAAAAElFTkSuQmCC";

const LONG_PARAGRAPH =
  "第二段从城门打开开始，节奏已经比较紧凑：守门人和巡夜人的对话控制在两个镜头以内，旁白只补充时间与地点。需要注意的是第 9 个镜头的提示词里同时写了「清晨」和「夜色」，生成时容易出现光线不一致，建议统一成「破晓前」。";

interface Entry {
  seq: number;
  type: "user" | "assistant" | "tool_result" | "system";
  uuid: string;
  timestamp: string;
  [key: string]: unknown;
}

function buildEntries(): Entry[] {
  const entries: Entry[] = [];
  const push = (entry: Omit<Entry, "seq" | "uuid" | "timestamp">) => {
    const seq = entries.length;
    entries.push({
      seq,
      uuid: `entry-${seq}`,
      timestamp: new Date(Date.parse("2026-01-01T06:00:00.000Z") + seq * 60_000).toISOString(),
      ...entry,
    } as Entry);
  };
  push({ type: "user", content: [{ type: "text", text: "把第 1 集重新拆成分镜，开场节奏收紧一些。" }] });
  push({
    type: "assistant",
    message_id: "m-1",
    content: [
      {
        type: "tool_use",
        id: "todo-1",
        name: "TodoWrite",
        input: {
          todos: [
            { content: "读取第 1 集剧本", activeForm: "正在读取第 1 集剧本", status: "completed" },
            { content: "拆分开场段落的镜头", activeForm: "正在拆分开场段落的镜头", status: "in_progress" },
            { content: "重写开场三个镜头的提示词", activeForm: "正在重写开场三个镜头的提示词", status: "pending" },
            { content: "核对镜头时长与旁白", activeForm: "正在核对镜头时长与旁白", status: "pending" },
            { content: "生成新的分镜图", activeForm: "正在生成新的分镜图", status: "pending" },
            { content: "整理改动清单交给你确认", activeForm: "正在整理改动清单", status: "pending" },
          ],
        },
      },
    ],
  });
  push({ type: "tool_result", tool_use_id: "todo-1", content: "Todos have been modified successfully." });
  push({ type: "assistant", message_id: "m-2", content: [{ type: "text", text: LONG_PARAGRAPH }] });
  return entries;
}

// Agent 的提问：一道单选、一道多选，选项带长说明。
const QUESTION = {
  question_id: "q-opening",
  questions: [
    {
      header: "首帧",
      question: "开场第一个镜头用哪张图作首帧？两张图的光线差别比较大，选定后后面三个镜头会跟着统一色调。",
      multiSelect: false,
      options: [
        { label: "雨夜城墙远景", description: "整体压暗，箭楼只露轮廓，适合慢慢推近的开场。" },
        { label: "巡夜人提灯特写", description: "人物先出场，灯光是画面里唯一的暖色。" },
        { label: "其他", description: "" },
      ],
    },
    {
      header: "审片",
      question: "审片时重点看哪些方面？",
      multiSelect: true,
      options: [
        { label: "光线是否连贯", description: "" },
        { label: "镜头节奏", description: "" },
        { label: "旁白与画面是否对得上", description: "" },
      ],
    },
  ],
};

/** running 会话的时间线与提问都从 entry 流回放；retry 拉长重连间隔，避免测试期间反复重放。 */
function buildStream(): string {
  const lines = ["retry: 30000", ""];
  for (const entry of buildEntries()) {
    lines.push(`id: ${entry.seq}`, "event: entry", `data: ${JSON.stringify(entry)}`, "");
  }
  lines.push("event: question", `data: ${JSON.stringify(QUESTION)}`, "", "");
  return lines.join("\n");
}

// 回复进行中连发 20 条：长短交替，长的占满两行截断。
const QUEUED_MESSAGES = Array.from({ length: 20 }, (_, index) => ({
  id: `queued-${index + 1}`,
  content: [
    {
      type: "text",
      text:
        index % 2 === 0
          ? `第 ${index + 1} 条：第 ${index + 3} 个镜头改成黄昏，巡夜人的提灯要成为画面里唯一的暖色，旁白放慢半拍，和城门打开的声音对齐。`
          : `第 ${index + 1} 条：结尾再加一个空镜`,
    },
  ],
  timestamp: "2026-01-01T07:59:00.000Z",
  state: "queued",
}));

/** running 会话：时间线从 entry 流回放，开场的排队消息快照里有 20 条消息；`withQuestion` 时 Agent 同时在提问。 */
function buildQueueStream(withQuestion = false): string {
  const lines = ["retry: 30000", ""];
  for (const entry of buildEntries()) {
    lines.push(`id: ${entry.seq}`, "event: entry", `data: ${JSON.stringify(entry)}`, "");
  }
  lines.push(
    "event: queue",
    `data: ${JSON.stringify({ session_id: QUEUE_SESSION_ID, messages: QUEUED_MESSAGES })}`,
    "",
    "event: status",
    `data: ${JSON.stringify({ session_id: QUEUE_SESSION_ID, status: "running" })}`,
    "",
    "",
  );
  if (withQuestion) lines.push("event: question", `data: ${JSON.stringify(QUESTION)}`, "", "");
  return lines.join("\n");
}

function session(id: string, title: string, status: string, updatedAt: string) {
  return { id, project_name: "demo", title, status, created_at: "2026-01-01T05:00:00.000Z", updated_at: updatedAt };
}

// 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在录制的项目数据上。
const EVENTS_STREAM: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

const ASK_SESSION = session(ASK_SESSION_ID, "重新拆分第 1 集分镜", "running", "2026-01-01T07:58:00.000Z");

const ASK_API: ApiOverrides = {
  ...EVENTS_STREAM,
  [`GET ${SESSIONS_PATH}`]: { status: 200, body: { sessions: [ASK_SESSION] } },
  [`GET ${SESSIONS_PATH}/${ASK_SESSION_ID}`]: { status: 200, body: { session: ASK_SESSION } },
  [`GET ${SESSIONS_PATH}/${ASK_SESSION_ID}/entries/stream`]: { status: 200, body: buildStream() },
};

const QUEUE_SESSION = session(QUEUE_SESSION_ID, "重新拆分第 1 集分镜", "running", "2026-01-01T07:59:00.000Z");

const QUEUE_API: ApiOverrides = {
  ...EVENTS_STREAM,
  [`GET ${SESSIONS_PATH}`]: { status: 200, body: { sessions: [QUEUE_SESSION] } },
  [`GET ${SESSIONS_PATH}/${QUEUE_SESSION_ID}`]: { status: 200, body: { session: QUEUE_SESSION } },
  [`GET ${SESSIONS_PATH}/${QUEUE_SESSION_ID}/entries/stream`]: { status: 200, body: buildQueueStream() },
};

const QUEUE_ASK_API: ApiOverrides = {
  ...QUEUE_API,
  [`GET ${SESSIONS_PATH}/${QUEUE_SESSION_ID}/entries/stream`]: { status: 200, body: buildQueueStream(true) },
};

const HISTORY_TITLES = [
  "第 1 集开场节奏",
  "把第 3 集的旁白全部改成第一人称，语气更克制一些，同时检查每个镜头的时长是否还和旁白长度对得上",
  "导入原著第 6–9 章",
  "角色设定：巡夜人",
  "",
  "生成第 2 集分镜图",
  "统一全片色调",
];
const HISTORY_STATUSES = ["idle", "running", "error", "interrupted", "completed"];
const HISTORY_SESSIONS = Array.from({ length: 28 }, (_, index) =>
  session(
    `session-${index}`,
    HISTORY_TITLES[index % HISTORY_TITLES.length],
    index === 0 ? "idle" : HISTORY_STATUSES[index % HISTORY_STATUSES.length],
    new Date(Date.parse("2026-01-01T07:59:30.000Z") - index * 7 * 3_600_000).toISOString(),
  ),
);

const HISTORY_API: ApiOverrides = {
  ...EVENTS_STREAM,
  [`GET ${SESSIONS_PATH}`]: { status: 200, body: { sessions: HISTORY_SESSIONS } },
  [`GET ${SESSIONS_PATH}/${IDLE_SESSION_ID}`]: { status: 200, body: { session: HISTORY_SESSIONS[0] } },
  ...idleSessionApi(SESSIONS_PATH, IDLE_SESSION_ID, buildEntries()),
};

const agentPanel = (page: Page) => page.getByRole("complementary", { name: "Agent 面板" });
const transcript = (page: Page) => page.getByRole("region", { name: "对话记录" });
const questionnaire = (page: Page) => page.getByRole("form", { name: "Agent 的提问" });
const todoRow = (page: Page) => page.getByRole("button", { name: /正在拆分开场段落的镜头.*1\/6/ });
const agentInput = (page: Page) => page.getByRole("combobox", { name: "Agent 输入" });
const queueTray = (page: Page) => page.getByRole("list", { name: "排队消息" });

async function questionReady(page: Page) {
  await questionnaire(page).waitFor();
  await todoRow(page).waitFor();
  await page.getByText("生成时容易出现光线不一致").waitFor();
}

async function queueReady(page: Page) {
  await expect(queueTray(page).getByRole("listitem")).toHaveCount(QUEUED_MESSAGES.length);
  await page.getByText("生成时容易出现光线不一致").waitFor();
}

async function historyReady(page: Page) {
  await transcript(page).waitFor();
  await page.getByText("生成时容易出现光线不一致").waitFor();
}

async function withPanelWidth(page: Page, width: number, ready: (page: Page) => Promise<void>) {
  await page.evaluate(([key, value]) => localStorage.setItem(key, value), [PANEL_WIDTH_KEY, String(width)]);
  await page.reload();
  await ready(page);
  await expect.poll(async () => (await agentPanel(page).boundingBox())?.width).toBeCloseTo(width, 0);
}


async function openHistory(page: Page) {
  await agentPanel(page).getByRole("button", { name: "会话历史" }).click();
  await page.getByRole("searchbox", { name: "搜索会话" }).waitFor();
}

defineRegionScenarios("Agent 输入区", [
  {
    name: "Agent 提问时问卷占用输入框的位置，待办进度显示在它上方",
    path: EPISODE_PATH,
    api: ASK_API,
    ready: questionReady,
    act: async (page) => {
      await expect(agentInput(page)).toBeHidden();
      await expect(questionnaire(page).getByRole("radio", { name: /雨夜城墙远景/ })).toBeVisible();
      // Agent 自带的「其他」由自由输入框代替
      await expect(questionnaire(page).getByRole("radio", { name: "其他" })).toHaveCount(0);
    },
    screenshot: { name: "agent-composer-question", target: agentPanel },
  },
  {
    name: "面板最窄 320px 时，问卷与待办进度同时出现，上方仍能看到消息",
    path: EPISODE_PATH,
    api: ASK_API,
    ready: questionReady,
    act: async (page) => {
      await withPanelWidth(page, 320, questionReady);
      await expect(todoRow(page)).toBeInViewport();
      await expect(questionnaire(page).getByRole("button", { name: "下一题" })).toBeInViewport();
      const flowHeight = (await transcript(page).boundingBox())?.height ?? 0;
      expect(flowHeight, "问卷与待办之上仍要留出消息区").toBeGreaterThan(48);
    },
  },
  {
    name: "展开待办清单后问卷压缩题目区，按钮仍可用",
    path: EPISODE_PATH,
    api: ASK_API,
    ready: questionReady,
    act: async (page) => {
      await todoRow(page).click();
      const list = page.getByRole("region", { name: "待办清单" });
      await expect(list).toContainText("整理改动清单交给你确认");
      await waitForEntrance(list);
      await expect(questionnaire(page).getByRole("button", { name: "下一题" })).toBeInViewport();
    },
  },
  {
    name: "回复进行中排队 20 条消息：托盘在自己的区域里滚动，停在最新一条，输入框与停止按钮仍在面板内",
    path: EPISODE_PATH,
    api: QUEUE_API,
    ready: queueReady,
    act: async (page) => {
      await expect(queueTray(page).getByText("第 20 条：结尾再加一个空镜")).toBeInViewport();
      await expect(queueTray(page).getByText(/^第 1 条：/)).not.toBeInViewport();
      await expect(agentInput(page)).toBeInViewport();
      await expect(agentPanel(page).getByRole("button", { name: "停止回复", exact: true })).toBeInViewport();
      const flowHeight = (await transcript(page).boundingBox())?.height ?? 0;
      expect(flowHeight, "托盘之上仍要留出消息区").toBeGreaterThan(48);
    },
  },
  {
    name: "Agent 提问期间排队 20 条消息：托盘与问卷都在面板内，托盘里没有立即发送",
    path: EPISODE_PATH,
    api: QUEUE_ASK_API,
    ready: async (page) => {
      await queueReady(page);
      await questionnaire(page).waitFor();
    },
    act: async (page) => {
      await expect(queueTray(page).getByText("第 20 条：结尾再加一个空镜")).toBeInViewport();
      await expect(queueTray(page).getByRole("button", { name: "立即发送" })).toHaveCount(0);
      await expect(queueTray(page).getByRole("button", { name: "编辑" }).last()).toBeInViewport();
      await expect(questionnaire(page).getByRole("button", { name: "下一题" })).toBeInViewport();
      const flowHeight = (await transcript(page).boundingBox())?.height ?? 0;
      expect(flowHeight, "问卷与托盘之上仍要留出消息区").toBeGreaterThan(48);
    },
  },
  {
    name: "打开会话历史：长列表在自己的区域里滚动",
    path: EPISODE_PATH,
    api: HISTORY_API,
    ready: historyReady,
    act: async (page) => {
      await openHistory(page);
      await expect(page.getByRole("button", { name: /第 1 集开场节奏/ }).first()).toHaveAttribute(
        "aria-current",
        "true",
      );
    },
    screenshot: { name: "agent-composer-history", target: agentPanel },
  },
  {
    name: "搜索会话没有匹配时说明原因",
    path: EPISODE_PATH,
    api: HISTORY_API,
    ready: historyReady,
    act: async (page) => {
      await openHistory(page);
      await page.getByRole("searchbox", { name: "搜索会话" }).fill("不存在的会话标题");
      await expect(agentPanel(page).getByRole("status")).toBeVisible();
    },
  },
  {
    name: "删除会话前确认",
    path: EPISODE_PATH,
    api: HISTORY_API,
    ready: historyReady,
    act: async (page) => {
      await openHistory(page);
      await page.getByRole("button", { name: /^删除会话「把第 3 集的旁白/ }).first().click();
      const dialog = page.getByRole("alertdialog");
      await dialog.waitFor();
      await waitForEntrance(dialog);
      await expect(dialog.getByRole("button", { name: "取消" })).toBeFocused();
    },
  },
  {
    name: "输入 / 打开斜杠命令菜单",
    path: EPISODE_PATH,
    api: HISTORY_API,
    ready: historyReady,
    act: async (page) => {
      await agentInput(page).click();
      await page.keyboard.type("/");
      const menu = page.getByRole("listbox", { name: "技能命令" });
      await menu.waitFor();
      await waitForEntrance(menu);
      await expect(agentInput(page)).toHaveAttribute("aria-expanded", "true");
    },
    screenshot: { name: "agent-composer-slash", target: agentPanel },
  },
  {
    name: "附加 5 张图片并写入长文本",
    path: EPISODE_PATH,
    api: HISTORY_API,
    ready: historyReady,
    act: async (page) => {
      const buffer = Buffer.from(IMAGE_DATA, "base64");
      await page.getByLabel("上传附件图片").setInputFiles(
        Array.from({ length: 5 }, (_, index) => ({ name: `ref-${index + 1}.png`, mimeType: "image/png", buffer })),
      );
      await expect(agentPanel(page).getByRole("button", { name: /^放大图片附件/ })).toHaveCount(5);
      await agentInput(page).fill(`${LONG_PARAGRAPH}\n${LONG_PARAGRAPH}`);
      await expect(page.getByRole("button", { name: "附加图片" })).toHaveCount(0);
    },
    screenshot: { name: "agent-composer-attachments", target: agentPanel },
  },
]);
