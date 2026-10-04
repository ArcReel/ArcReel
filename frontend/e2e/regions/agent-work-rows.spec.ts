import type { Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// Agent 面板的工序行：ArcReel 工具的显示名与摘要、失败与运行中的收起状态、长结果的截断与
// 「显示全部」、子智能体的子时间线与结论。会话是手写的压力数据：长命令、长结果、长错误、
// 多条目的子时间线。
const EPISODE_PATH = "/app/projects/demo/episodes/1";
const SESSION_ID = "session-work-rows";
const SESSIONS_PATH = "/api/v1/projects/demo/assistant/sessions";
const PANEL_WIDTH_KEY = "arcreel_assistant_panel_width";

const LONG_COMMAND =
  "ffprobe -v error -select_streams v:0 -show_entries stream=width,height,duration,r_frame_rate -of json videos/E1S01_v3.mp4 videos/E1S02_v2.mp4 videos/E1S03_v1.mp4";
const LONG_RESULT = Array.from(
  { length: 40 },
  (_, i) => `E1S${String(i + 1).padStart(2, "0")}  1080x1920  duration=${(4 + (i % 5) * 0.5).toFixed(1)}s  r_frame_rate=24/1  codec=h264  bitrate=8.2Mb/s`,
).join("\n");
const LONG_ERROR = `PermissionError: 文件围栏拒绝读取项目目录之外的路径 /data/projects/other-project/scripts/episode_1.json。${"只能读取当前项目 demo 下的业务文件，越界路径不会被读取。".repeat(6)}`;

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
      timestamp: new Date(Date.parse("2026-01-01T06:00:00.000Z") + seq * 30_000).toISOString(),
      ...entry,
    } as Entry);
  };
  const toolUse = (id: string, name: string, input: Record<string, unknown>, parent?: string) =>
    push({
      type: "assistant",
      message_id: `m-${id}`,
      content: [{ type: "tool_use", id, name, input }],
      ...(parent ? { parent_tool_use_id: parent } : {}),
    });
  const toolResult = (id: string, content: string, isError = false, parent?: string) =>
    push({
      type: "tool_result",
      tool_use_id: id,
      content,
      is_error: isError,
      ...(parent ? { parent_tool_use_id: parent } : {}),
    });

  push({ type: "user", content: [{ type: "text", text: "生成第 1 集缺的分镜图，再检查一下已有视频的规格。" }] });
  push({
    type: "assistant",
    message_id: "m-think",
    content: [{ type: "thinking", thinking: "先看制作计划，确认缺哪些分镜图。", signature: "sig" }],
  });
  toolUse("plan", "mcp__arcreel__get_workflow_plan", { episode_id: 1 });
  toolResult("plan", "{\"next_action\": \"generate_storyboards\"}");
  toolUse("boards", "mcp__arcreel__generate_storyboards", {
    script: "episode_1.json",
    segment_ids: ["E1S01", "E1S02", "E1S03", "E1S04", "E1S05", "E1S06"],
  });
  toolResult("boards", "{\"generation_batch\": {\"batch_id\": \"batch-7c1e\", \"queued\": 6}}");
  toolUse("probe", "Bash", { command: LONG_COMMAND });
  toolResult("probe", LONG_RESULT);
  toolUse("read-other", "Read", { file_path: "/data/projects/other-project/scripts/episode_1.json" });
  toolResult("read-other", LONG_ERROR, true);
  toolUse("skill", "Skill", { skill: "review-footage", args: "第 1 集全部视频单元" });
  toolResult("skill", "Launching skill: review-footage");

  // 子智能体：内部消息带 parent_tool_use_id，归进锚点 tool_use 的子时间线
  toolUse("agent", "Agent", { subagent_type: "review-footage", description: "审阅第 1 集的视频单元", prompt: "逐个看联系表" });
  push({
    type: "user",
    parent_tool_use_id: "agent",
    content: [{ type: "text", text: "逐个看第 1 集视频单元的联系表，指出画面问题。" }],
  });
  toolUse("sub-inspect", "mcp__arcreel__inspect_video_units", { unit_ids: ["E1S01", "E1S02", "E1S03", "E1S04"] }, "agent");
  toolResult("sub-inspect", "{\"frames\": 32}", false, "agent");
  toolUse("sub-read", "mcp__arcreel__read_project_file", { path: "scripts/episode_1.json" }, "agent");
  toolResult("sub-read", "{\"segments\": []}", false, "agent");
  push({
    type: "assistant",
    message_id: "m-sub-text",
    parent_tool_use_id: "agent",
    content: [{ type: "text", text: "E1S03 的人物站位与 E1S02 相反，剪在一起会跳轴。" }],
  });
  toolResult(
    "agent",
    "审阅结论：\n\n- E1S03 与 E1S02 人物站位相反，建议重新生成 E1S03。\n- E1S05 的光线偏暖，与雨夜基调不一致。",
  );

  // 没有锚点的后台任务
  push({ type: "system", subtype: "task_started", task_id: "task-1", description: "批量生成分镜图" });
  push({
    type: "system",
    subtype: "task_notification",
    task_id: "task-1",
    task_status: "failed",
    description: "批量生成分镜图",
    summary: "图片供应商拒绝了 2 个请求，其余 4 张已生成",
  });

  toolUse("timeline", "mcp__arcreel__edit_timeline", {
    timeline: "tl-3f9a0c21",
    base_revision: 3,
    summary: "把 E1S03 换成重新生成的版本，压低开场原声",
    operations: [{ op: "set_volume", clip: "c1", volume: 0.4 }],
  });
  toolResult("timeline", "{\"revision\": 4}");
  push({
    type: "assistant",
    message_id: "m-reply",
    content: [{ type: "text", text: "分镜图已入队，审片发现 E1S03 需要重新生成，剪辑时间线也已经换上新版本。" }],
  });
  // 还没有结果的工具：会话没有终结，显示为运行中
  toolUse("pending", "mcp__arcreel__generate_videos", {
    script: "episode_1.json",
    target: { scope: "selected", ids: ["E1S03"] },
  });
  return entries;
}

const SESSION = {
  id: SESSION_ID,
  project_name: "demo",
  title: "第 1 集分镜与审片",
  status: "idle",
  created_at: "2026-01-01T06:00:00.000Z",
  updated_at: "2026-01-01T07:00:00.000Z",
};

const API: ApiOverrides = {
  // 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在录制的项目数据上。
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
  [`GET ${SESSIONS_PATH}`]: { status: 200, body: { sessions: [SESSION] } },
  [`GET ${SESSIONS_PATH}/${SESSION_ID}`]: { status: 200, body: { session: SESSION } },
  [`GET ${SESSIONS_PATH}/${SESSION_ID}/entries`]: {
    status: 200,
    body: { session_id: SESSION_ID, status: "idle", entries: buildEntries(), draft: null, draft_rev: 0 },
  },
};

const agentPanel = (page: Page) => page.getByRole("complementary", { name: "Agent 面板" });
const transcript = (page: Page) => page.getByRole("region", { name: "对话记录" });
const row = (page: Page, name: RegExp) => transcript(page).getByRole("button", { name });

async function rowsReady(page: Page) {
  await transcript(page).waitFor();
  await row(page, /生成视频/).waitFor();
  await page.waitForLoadState("networkidle");
}

async function withPanelWidth(page: Page, width: number) {
  await page.evaluate(([key, value]) => localStorage.setItem(key, value), [PANEL_WIDTH_KEY, String(width)]);
  await page.reload();
  await rowsReady(page);
  await expect.poll(async () => (await agentPanel(page).boundingBox())?.width).toBeCloseTo(width, 0);
}

/** 展开区里没有任何元素自己滚动：长文本截断后整段由消息区滚动。 */
async function expectNoInnerScroll(page: Page, rowName: RegExp) {
  const trigger = row(page, rowName);
  const panelId = await trigger.getAttribute("aria-controls");
  expect(panelId).toBeTruthy();
  const scrollers = await page.locator(`[id="${panelId}"]`).evaluate((panel) =>
    [panel, ...panel.querySelectorAll<HTMLElement>("*")].filter((el) => {
      const { overflowY } = getComputedStyle(el);
      return (overflowY === "auto" || overflowY === "scroll") && el.scrollHeight > el.clientHeight;
    }).length,
  );
  expect(scrollers).toBe(0);
}

defineRegionScenarios("Agent 工序行", [
  {
    name: "工序行默认收起：工具显示本地化名称与摘要，失败与运行中在收起状态即可辨认",
    path: EPISODE_PATH,
    api: API,
    ready: rowsReady,
    act: async (page) => {
      await expect(row(page, /读取文件.*失败/)).toHaveAttribute("aria-expanded", "false");
      await expect(row(page, /生成视频.*第一集 · S03.*运行中/)).toBeVisible();
      await expect(row(page, /修改剪辑时间线.*压低开场原声/)).toBeVisible();
      // 原始英文工具名不出现在界面上
      await expect(transcript(page).getByText(/mcp__arcreel__/)).toHaveCount(0);
    },
    screenshot: { name: "agent-work-rows-collapsed", target: agentPanel },
  },
  {
    name: "展开长结果：只显示前 10 行，「显示全部」后整段展开，不产生内层滚动",
    path: EPISODE_PATH,
    api: API,
    ready: rowsReady,
    act: async (page) => {
      const bash = row(page, /运行命令/);
      await bash.scrollIntoViewIfNeeded();
      await bash.click();
      await expect(transcript(page).getByText(/E1S10 {2}1080x1920/)).toBeVisible();
      await expect(transcript(page).getByText(/E1S40 {2}1080x1920/)).toHaveCount(0);
      await transcript(page).getByRole("button", { name: "显示全部" }).click();
      await expect(transcript(page).getByText(/E1S40 {2}1080x1920/)).toBeVisible();
      await expectNoInnerScroll(page, /运行命令/);

      const failed = row(page, /读取文件.*失败/);
      await failed.click();
      await expect(transcript(page).getByText(/PermissionError/)).toBeVisible();
      await expectNoInnerScroll(page, /读取文件/);
    },
  },
  {
    name: "面板最窄 320px，展开子智能体：子时间线的工序行与子智能体结论",
    path: EPISODE_PATH,
    api: API,
    ready: rowsReady,
    act: async (page) => {
      await withPanelWidth(page, 320);
      const subagent = row(page, /子智能体.*审阅第 1 集的视频单元/);
      await subagent.scrollIntoViewIfNeeded();
      await subagent.click();
      await expect(row(page, /查看视频单元画面.*第一集 · S01、S02、S03 等 4 个/)).toBeVisible();
      await expect(transcript(page).getByText("子智能体结论")).toBeVisible();
      await expect(transcript(page).getByText(/建议重新生成 E1S03/)).toBeVisible();
    },
    screenshot: { name: "agent-work-rows-subagent", target: agentPanel },
  },
]);
