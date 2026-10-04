import type { Locator, Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// Agent 面板消息区：滚动跟随与「跳到最新」、气泡与正文限宽、Markdown 代码块与宽表格、
// 原地编辑与图片放大。会话是手写的压力数据：长文本、长链接、多轮对话。
const EPISODE_PATH = "/app/projects/demo/episodes/1";
const SESSION_ID = "session-stress";
const SESSIONS_PATH = "/api/v1/projects/demo/assistant/sessions";
const PANEL_WIDTH_KEY = "arcreel_assistant_panel_width";

// 160×96 的双色方格 PNG，缩略图里看得出是一张图。
const IMAGE_DATA =
  "iVBORw0KGgoAAAANSUhEUgAAAKAAAABgCAIAAAAVRe7OAAAA+UlEQVR42u3bQQ0AIAwDwKnihQI08EAVeidjYVxSA+29G3vdkox5SvJb3wAMGDBgwIABAwYMGDBgwIABAwYMGDBgwIABAwYMGDBgwIABAwYMGDBgwIABAwYMGDBgwIABAwYMGDBgwIABAwbcCtjQvfsCBgwYMGDAgAEDBgwYMGDAgAEDBgwYMGDAgAEDBgwYMGDAgAEDBgwYMGDAgAEDBgwYMGDAgAEDBgwYMGDAgAED7gVsaA9/wIABAwYMGDBgwIABAwYMGDBgwIABAwYMGDBgwIABAwYMGDBgwIABAwYMGDBgwIABAwYMGDBgwIABAwYMGDBgwM/0TQmWp5SVOq3vAAAAAElFTkSuQmCC";

const REVIEW_REPLY = `## 开场节奏

开场的雨夜一共 6 个镜头，平均时长 4.2 秒，比后面段落长了将近一倍。问题主要在三处：

1. 巡夜人提灯走过箭楼的长镜头占了 9 秒，画面信息只有一个动作。
2. 城墙空镜和旗子特写都在交代同一件事。
3. 旁白在第 4 个镜头才出现，前 15 秒没有任何人物信息。

建议把 \`shot_03\` 和 \`shot_04\` 合并，改动如下：

\`\`\`json
{ "shots": [{ "id": "shot_03", "duration_seconds": 4, "prompt": "巡夜人提灯走过箭楼，镜头从城墙顶端缓慢下摇，雨线在灯光里清晰可见，远处城门半掩" }] }
\`\`\`

| 镜头 | 现时长 | 建议时长 | 处理方式 | 备注说明 |
| --- | --- | --- | --- | --- |
| shot_01 | 4 秒 | 3 秒 | 保留 | 雨夜空镜，整体压暗 |
| shot_02 | 5 秒 | 3 秒 | 保留 | 箭楼远景，去掉推镜 |
| shot_03 | 9 秒 | 4 秒 | 合并 | 与 shot_04 合成一个下摇镜头 |`;

const LONG_PARAGRAPH =
  "第二段从城门打开开始，节奏已经比较紧凑：守门人和巡夜人的对话控制在两个镜头以内，旁白只补充时间与地点。需要注意的是第 9 个镜头的提示词里同时写了「清晨」和「夜色」，生成时容易出现光线不一致，建议统一成「破晓前」。另外第 11 个镜头的人物站位与第 10 个镜头相反，剪在一起会有跳轴的感觉。";

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
  const say = (text: string) => push({ type: "user", content: [{ type: "text", text }] });
  const reply = (messageId: string, content: unknown[]) => push({ type: "assistant", message_id: messageId, content });

  push({
    type: "user",
    content: [
      { type: "image", source: { type: "base64", media_type: "image/png", data: IMAGE_DATA } },
      { type: "text", text: "帮我看看第 1 集的分镜，开场节奏是不是太慢了？参考图是上一版的开场构图。" },
    ],
  });
  reply("m-1", [
    {
      type: "thinking",
      thinking: "用户关心开场节奏。先读第 1 集剧本里各镜头的时长，再和后面段落的平均值比较。",
      signature: "sig",
    },
    { type: "tool_use", id: "tool-read-1", name: "Read", input: { file_path: "scripts/episode_1.json" } },
  ]);
  push({ type: "tool_result", tool_use_id: "tool-read-1", content: "{\"shots\": []}" });
  reply("m-2", [{ type: "text", text: REVIEW_REPLY }]);
  say(
    "参考这篇分析再看看第二段：https://example.com/storyboard/episode-1/opening-sequence/rhythm-analysis?version=20260101&section=night-patrol-and-city-gate 另外第二段里守门人那几句旁白是不是也可以删掉一些，我觉得信息有点重复，观众在画面里已经能看出时间和地点了。",
  );
  reply("m-3", [{ type: "text", text: LONG_PARAGRAPH }]);
  say("先把开场三个镜头的提示词改掉。");
  reply("m-4", [{ type: "text", text: "好的，我先改 shot_03 的提示词，再" }]);
  push({ type: "system", subtype: "interrupt" });
  for (let round = 1; round <= 3; round += 1) {
    say(`继续第 ${round} 轮检查：只看第 ${round + 1} 段的镜头衔接。`);
    reply(`m-round-${round}`, [{ type: "text", text: `${LONG_PARAGRAPH}\n\n${LONG_PARAGRAPH}` }]);
  }
  say("继续，但只改开场三个镜头。");
  reply("m-last", [
    {
      type: "text",
      text: "开场三个镜头已经改好：\n\n- `shot_01` 压暗到雨夜的基调，时长改为 3 秒。\n- `shot_02` 去掉推镜，保留箭楼远景。\n- `shot_03` 与 `shot_04` 合成一个下摇镜头，时长 4 秒。",
    },
  ]);
  return entries;
}

const API: ApiOverrides = {
  // 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在录制的项目数据上。
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
  [`GET ${SESSIONS_PATH}`]: {
    status: 200,
    body: {
      sessions: [
        {
          id: SESSION_ID,
          project_name: "demo",
          title: "第 1 集开场节奏",
          status: "idle",
          created_at: "2026-01-01T06:00:00.000Z",
          updated_at: "2026-01-01T07:00:00.000Z",
        },
      ],
    },
  },
  [`GET ${SESSIONS_PATH}/${SESSION_ID}`]: {
    status: 200,
    body: {
      session: {
        id: SESSION_ID,
        project_name: "demo",
        title: "第 1 集开场节奏",
        status: "idle",
        created_at: "2026-01-01T06:00:00.000Z",
        updated_at: "2026-01-01T07:00:00.000Z",
      },
    },
  },
  [`GET ${SESSIONS_PATH}/${SESSION_ID}/entries`]: {
    status: 200,
    body: { session_id: SESSION_ID, status: "idle", entries: buildEntries(), draft: null, draft_rev: 0 },
  },
};

const agentPanel = (page: Page) => page.getByRole("complementary", { name: "Agent 面板" });
const transcript = (page: Page) => page.getByRole("region", { name: "对话记录" });
const jumpToLatest = (page: Page) => page.getByRole("button", { name: "跳到最新", includeHidden: true });

async function messagesReady(page: Page) {
  await transcript(page).waitFor();
  await page.getByText("开场三个镜头已经改好").waitFor();
  // Markdown 渲染器按需加载，等它把代码块与表格渲染出来
  await page.getByRole("region", { name: "代码块" }).waitFor();
  await page.getByRole("table", { name: "表格" }).waitFor();
  // 代码块的高亮组件按需加载，加载完会替换掉先渲染的代码块；等它落定再交互与截图
  await page.waitForLoadState("networkidle");
}

/** 视口停在最底部（误差 1px 以内）。 */
async function expectAtEnd(page: Page) {
  await expect
    .poll(() =>
      transcript(page).evaluate((el) => Math.abs(el.scrollHeight - el.clientHeight - el.scrollTop) <= 1),
    )
    .toBe(true);
}

async function withPanelWidth(page: Page, width: number) {
  await page.evaluate(([key, value]) => localStorage.setItem(key, value), [PANEL_WIDTH_KEY, String(width)]);
  await page.reload();
  await messagesReady(page);
  await expect.poll(async () => (await agentPanel(page).boundingBox())?.width).toBeCloseTo(width, 0);
}

/** 弹层进场动画结束后再探测，避免 axe 量到半透明的中间态。只等弹层自身：页面上持续运行的指示动画不会结束。 */
async function settleAnimations(dialog: Locator) {
  await dialog.evaluate((el) => Promise.allSettled(el.getAnimations({ subtree: true }).map((a) => a.finished)));
}

defineRegionScenarios("Agent 消息区", [
  {
    name: "打开长会话时停在最新一条，不显示「跳到最新」",
    path: EPISODE_PATH,
    api: API,
    ready: messagesReady,
    act: async (page) => {
      await expectAtEnd(page);
      await expect(jumpToLatest(page)).toHaveAttribute("data-active", "false");
    },
    screenshot: { name: "agent-messages-latest", target: agentPanel },
  },
  {
    name: "上翻后出现「跳到最新」，点击回到底部",
    path: EPISODE_PATH,
    api: API,
    ready: messagesReady,
    act: async (page) => {
      await transcript(page).hover();
      await page.mouse.wheel(0, -1600);
      await expect(jumpToLatest(page)).toHaveAttribute("data-active", "true");
      await jumpToLatest(page).click();
      await expectAtEnd(page);
      await expect(jumpToLatest(page)).toHaveAttribute("data-active", "false");
      // 停在按钮出现的状态做探测与截图
      await transcript(page).hover();
      await page.mouse.wheel(0, -1600);
      await expect(jumpToLatest(page)).toHaveAttribute("data-active", "true");
    },
    screenshot: { name: "agent-messages-jump", target: agentPanel },
  },
  {
    name: "面板最窄 320px 时，代码块与宽表格在自己的区域里横向滚动",
    path: EPISODE_PATH,
    api: API,
    ready: messagesReady,
    act: async (page) => {
      await withPanelWidth(page, 320);
      const code = page.getByRole("region", { name: "代码块" });
      await code.scrollIntoViewIfNeeded();
      await expect.poll(() => code.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);
      // 键盘聚焦后用方向键横向滚动
      await code.focus();
      await page.keyboard.press("ArrowRight");
      await expect.poll(() => code.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0);
    },
  },
  {
    name: "面板最宽 640px，展开思考过程",
    path: EPISODE_PATH,
    api: API,
    ready: messagesReady,
    act: async (page) => {
      await withPanelWidth(page, 640);
      const thinking = page.getByRole("button", { name: "思考过程" });
      await thinking.scrollIntoViewIfNeeded();
      await thinking.click();
      await expect(thinking).toHaveAttribute("aria-expanded", "true");
      await expect(page.getByText("先读第 1 集剧本里各镜头的时长")).toBeVisible();
    },
  },
  {
    name: "原地编辑已发送的消息",
    path: EPISODE_PATH,
    api: API,
    ready: messagesReady,
    act: async (page) => {
      await page.getByRole("button", { name: "编辑此消息并从这里重新发送" }).last().click();
      const editor = page.getByRole("textbox", { name: "改写消息内容" });
      await expect(editor).toBeFocused();
      await expect(editor).toHaveValue("继续，但只改开场三个镜头。");
      await expect(page.getByText("此消息之后的对话将被丢弃")).toBeVisible();
    },
    screenshot: { name: "agent-messages-editing", target: agentPanel },
  },
  {
    name: "放大用户消息里的图片附件",
    path: EPISODE_PATH,
    api: API,
    ready: messagesReady,
    act: async (page) => {
      const thumbnail = page.getByRole("button", { name: "放大图片附件 1" });
      await thumbnail.scrollIntoViewIfNeeded();
      await thumbnail.click();
      const dialog = page.getByRole("dialog", { name: "图片附件 1" });
      await dialog.waitFor();
      await settleAnimations(dialog);
    },
  },
]);
