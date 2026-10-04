import type { Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// Agent 消息区的会话投影：压缩续接摘要显示为「上下文已压缩」分隔线、缺锚点的子代理显示
// 推断出的描述与终态、Agent 失败显示为一句话结论的卡片。会话是手写的压力数据：很长的续接
// 摘要、很长的异常堆栈。
const EPISODE_PATH = "/app/projects/demo/episodes/1";
const SESSIONS_PATH = "/api/v1/projects/demo/assistant/sessions";
const PANEL_WIDTH_KEY = "arcreel_assistant_panel_width";

const SUMMARY_SECTION =
  "用户在审第 8 集的视频，要求逐个镜头核对时长与提示词，发现问题就直接改提示词并重新生成，生成前先说明改动。已经确认 shot_01 到 shot_06 的时长与剧本一致，shot_04 的光线描述从「黄昏」改成了「破晓前」，与前后镜头保持一致。";
const COMPACT_SUMMARY = [
  "This session is being continued from a previous conversation that ran out of context. The conversation is summarized below:",
  "",
  "## 主要请求",
  "",
  SUMMARY_SECTION,
  "",
  "## 已完成的工作",
  "",
  ...Array.from({ length: 6 }, (_, i) => `${i + 1}. ${SUMMARY_SECTION}`),
  "",
  "## 待办",
  "",
  "- 检查 shot_07 到 shot_12，并汇总需要重新生成的镜头。",
].join("\n");

const LONG_TRACEBACK = Array.from(
  { length: 40 },
  (_, i) =>
    `  File "/app/.venv/lib/python3.12/site-packages/claude_agent_sdk/_internal/transport/subprocess_cli.py", line ${200 + i}, in _read_messages_impl_with_a_rather_long_function_name`,
).join("\n");

const FAILURE = {
  version: 1,
  phase: "turn",
  timestamp: "2026-01-01T07:10:00.000Z",
  project_name: "demo",
  session_id: "session-failure",
  summary: {
    key: "rate_limit",
    source: "sdk_assistant",
    type: "rate_limit",
    status: 429,
    message: "API Error: 429 {\"type\":\"error\",\"error\":{\"type\":\"rate_limit_error\",\"message\":\"Number of request tokens has exceeded your per-minute rate limit\"}}",
  },
  raw: {
    assistant_message: {
      error: "rate_limit",
      content: [{ type: "text", text: "API Error: 429 rate_limit_error" }],
      traceback: LONG_TRACEBACK,
    },
  },
};

interface Entry {
  seq: number;
  type: "user" | "assistant" | "tool_result" | "system";
  uuid: string;
  timestamp: string;
  [key: string]: unknown;
}

function entryLog(build: (push: (entry: Omit<Entry, "seq" | "uuid" | "timestamp">) => void) => void): Entry[] {
  const entries: Entry[] = [];
  build((entry) => {
    const seq = entries.length;
    entries.push({
      seq,
      uuid: `entry-${seq}`,
      timestamp: new Date(Date.parse("2026-01-01T06:00:00.000Z") + seq * 60_000).toISOString(),
      ...entry,
    } as Entry);
  });
  return entries;
}

// 压缩续接后懒生成的会话：主线从续接摘要开始，摘要之前发起的审片子代理没有锚点，
// 子时间线与推断出的终态附在末尾。
const CONTINUED_ENTRIES = entryLog((push) => {
  push({ type: "user", subtype: "compact_summary", content: [{ type: "text", text: COMPACT_SUMMARY }] });
  push({ type: "user", content: [{ type: "text", text: "继续检查 shot_07 到 shot_12。" }] });
  push({
    type: "assistant",
    message_id: "m-1",
    content: [{ type: "text", text: "shot_07 到 shot_12 的时长都与剧本一致，shot_10 的提示词缺少人物站位，已经补上。" }],
  });
  const parent = { parent_tool_use_id: "tool-review-before-compact" };
  push({ type: "user", content: [{ type: "text", text: "审片：核对第 8 集 shot_01 到 shot_06\n逐个镜头比对时长与提示词。" }], ...parent });
  push({
    type: "assistant",
    message_id: "m-sub-1",
    content: [{ type: "text", text: "六个镜头的时长都与剧本一致，shot_04 的光线描述需要改成「破晓前」。" }],
    ...parent,
  });
  push({
    type: "system",
    subtype: "subagent_outcome",
    tool_use_id: "tool-review-before-compact",
    description: "审片：核对第 8 集 shot_01 到 shot_06",
    task_status: "completed",
    summary: "六个镜头的时长都与剧本一致，shot_04 的光线描述需要改成「破晓前」。",
  });
});

const FAILURE_ENTRIES = entryLog((push) => {
  push({ type: "user", content: [{ type: "text", text: "把 shot_04 重新生成一遍。" }] });
  push({ type: "assistant", message_id: "m-1", content: [{ type: "text", text: "好的，先提交 shot_04 的生成任务。" }] });
  push({ type: "system", subtype: "agent_turn_failure", failure: FAILURE });
});

function sessionApi(sessionId: string, title: string, entries: Entry[]): ApiOverrides {
  const session = {
    id: sessionId,
    project_name: "demo",
    title,
    status: "idle",
    created_at: "2026-01-01T06:00:00.000Z",
    updated_at: "2026-01-01T07:00:00.000Z",
  };
  return {
    // 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在录制的项目数据上。
    "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
    [`GET ${SESSIONS_PATH}`]: { status: 200, body: { sessions: [session] } },
    [`GET ${SESSIONS_PATH}/${sessionId}`]: { status: 200, body: { session } },
    [`GET ${SESSIONS_PATH}/${sessionId}/entries`]: {
      status: 200,
      body: { session_id: sessionId, status: "idle", entries, draft: null, draft_rev: 0 },
    },
  };
}

const CONTINUED_API = sessionApi("session-continued", "第 8 集审片", CONTINUED_ENTRIES);
const FAILURE_API = sessionApi("session-failure", "重新生成 shot_04", FAILURE_ENTRIES);

const agentPanel = (page: Page) => page.getByRole("complementary", { name: "Agent 面板" });
const transcript = (page: Page) => page.getByRole("region", { name: "对话记录" });
const failureCard = (page: Page) => page.getByRole("alert").filter({ hasText: "这一轮没有完成" });

/** 等消息渲染落定：Markdown 渲染器按需加载，离屏项按最终内容排版一次（同 agent-messages.spec.ts）。 */
async function settleTranscript(page: Page, lastText: string) {
  await transcript(page).waitFor();
  await page.getByText(lastText).first().waitFor();
  await page.waitForLoadState("networkidle");
  await transcript(page).evaluate(async (el) => {
    const items = [...el.querySelectorAll<HTMLElement>("[data-slot=message-scroller-item]")];
    const nextFrame = () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    for (const item of items) item.style.contentVisibility = "visible";
    await nextFrame();
    for (const item of items) item.style.contentVisibility = "";
    await nextFrame();
  });
}

const continuedReady = (page: Page) => settleTranscript(page, "审片：核对第 8 集 shot_01 到 shot_06");
const failureReady = (page: Page) => settleTranscript(page, "这一轮没有完成");

defineRegionScenarios("Agent 会话投影", [
  {
    name: "压缩续接的会话：缺锚点的审片子代理显示推断出的描述与完成状态",
    path: EPISODE_PATH,
    api: CONTINUED_API,
    ready: continuedReady,
    act: async (page) => {
      const card = page.getByRole("button", { name: /审片：核对第 8 集 shot_01 到 shot_06/ });
      // 工序行成功不加标记：完成的子智能体既不是「已停止」，也不是「运行中」
      await expect(card).not.toContainText("已停止");
      await expect(card).not.toContainText("运行中");
    },
    screenshot: { name: "agent-projection-continued", target: agentPanel },
  },
  {
    name: "续接摘要是「上下文已压缩」分隔线，展开后看到很长的摘要",
    path: EPISODE_PATH,
    api: CONTINUED_API,
    ready: continuedReady,
    act: async (page) => {
      const marker = page.getByRole("button", { name: "上下文已压缩" });
      await marker.scrollIntoViewIfNeeded();
      await expect(page.getByText("主要请求")).toHaveCount(0);
      await marker.click();
      await expect(marker).toHaveAttribute("aria-expanded", "true");
      await expect(page.getByRole("heading", { name: "主要请求" })).toBeVisible();
      await marker.scrollIntoViewIfNeeded();
    },
    screenshot: { name: "agent-projection-summary", target: agentPanel },
  },
  {
    name: "失败卡片只给一句话结论与操作，不露出原始错误码",
    path: EPISODE_PATH,
    api: FAILURE_API,
    ready: failureReady,
    act: async (page) => {
      await expect(failureCard(page)).toContainText("模型服务限制了请求频率。");
      await expect(failureCard(page)).not.toContainText("rate_limit");
      await expect(failureCard(page)).not.toContainText("429");
      await expect(failureCard(page).getByRole("link", { name: "Agent 设置" })).toBeVisible();
    },
    screenshot: { name: "agent-projection-failure", target: agentPanel },
  },
  {
    name: "面板最窄 320px 时展开失败详情，长堆栈折行不撑破面板",
    path: EPISODE_PATH,
    api: FAILURE_API,
    ready: failureReady,
    act: async (page) => {
      await page.evaluate((key) => localStorage.setItem(key, "320"), PANEL_WIDTH_KEY);
      await page.reload();
      await failureReady(page);
      const details = failureCard(page).getByRole("button", { name: "详情" });
      await details.click();
      await expect(details).toHaveAttribute("aria-expanded", "true");
      await expect(failureCard(page)).toContainText("rate_limit_error");
      await expect(page.getByTestId("failure-observation-json")).toContainText("subprocess_cli.py");
    },
  },
]);
