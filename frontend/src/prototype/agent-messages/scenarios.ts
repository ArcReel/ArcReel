// PROTOTYPE — 消息区原型（#2980）的样本场景：只改内存里的 assistant store，不调用后端。
// 真实会话缺的消息类型（流式、待办、提问、失败、中断、图片）在这里按真实形状补一份。
// 「复原」回到注入前的真实会话。

import { useAssistantStore } from "@/stores/assistant-store";
import { useAppStore } from "@/stores/app-store";
import type { ContentBlock, FailureObservation, PendingQuestion, TodoItem, Turn } from "@/types";

let snapshot: { turns: Turn[]; status: ReturnType<typeof useAssistantStore.getState>["sessionStatus"] } | null = null;
let timers: number[] = [];
let seq = 0;

const nowIso = () => new Date().toISOString();
const uid = (p: string) => `proto-${p}-${++seq}`;

function keepSnapshot() {
  if (snapshot) return;
  const s = useAssistantStore.getState();
  snapshot = { turns: s.turns, status: s.sessionStatus };
}

function appendTurns(...extra: Turn[]) {
  keepSnapshot();
  const s = useAssistantStore.getState();
  useAssistantStore.setState({ turns: [...s.turns, ...extra] });
}

function clearTimers() {
  for (const t of timers) window.clearTimeout(t);
  timers = [];
}

export function restoreScenario() {
  clearTimers();
  const s = useAssistantStore.getState();
  if (snapshot) {
    useAssistantStore.setState({ turns: snapshot.turns, sessionStatus: snapshot.status });
  }
  useAssistantStore.setState({ draftTurn: null, pendingQuestion: null, startupFailure: null, startupFailureOrigin: null });
  useAppStore.getState().setFocusedContext(null);
  snapshot = null;
  void s;
}

// ---------------------------------------------------------------------------
// 流式回放：发一条提问 → 思考 → 工具 → 待办 → 长正文逐字增长 → 再一个工具 → 收尾
// ---------------------------------------------------------------------------

const STREAM_TEXT = `第 8 集《十三富豪的死刑名单》剩下的 10 段视频我已经按顺序排进生成队列，先说结论：**预计 6 分钟左右全部完成，费用约 ¥18.40**。

**这一轮要做的事**

- U02–U06：滑膛进入总统大厅后的对峙，五段都沿用场景资产「豪华酒店总统大厅」作为首帧参考。
- U07–U10：朱汉杨讲述「南半球天空」的回忆段落，画面切到夜空，参考图换成场景资产「南半球天空」。
- U12：结尾的反打镜头，需要等 U11 生成完成后取尾帧作为参考，所以它会最后一个开始。

**生成时需要你留意的地方**

1. U07 的提示词里写了「镜头缓慢上摇到银河」，wan3.0-video 对长时间运镜偶尔会抖动；如果出来的画面不稳，我会把时长从 12 秒拆成两段 6 秒。
2. U09 有一句台词超过 40 个字，口型可能对不上。生成完我会抽帧检查，必要时把台词拆到 U10。
3. 供应商 custom-3 的并发上限是 3，所以队列会分四批跑完，中途刷新页面不影响进度。

生成结束后我会再派两个审片子代理分别检查前后两半，把需要重做的片段列出来给你确认。`;

const TODOS_RUNNING: TodoItem[] = [
  { content: "核对第 8 集视频单元的参考图", activeForm: "正在核对参考图", status: "completed" },
  { content: "生成 U02–U06", activeForm: "正在生成 U02–U06", status: "completed" },
  { content: "生成 U07–U10", activeForm: "正在生成 U07–U10", status: "in_progress" },
  { content: "取 U11 尾帧并生成 U12", activeForm: "正在生成 U12", status: "pending" },
  { content: "派子代理审片并汇总结论", activeForm: "正在审片", status: "pending" },
];

export function playStreaming(opts: { userText?: string } = {}) {
  clearTimers();
  keepSnapshot();
  const s = useAssistantStore.getState();
  const userTurn: Turn = {
    type: "user",
    uuid: uid("user"),
    timestamp: nowIso(),
    content: [{ type: "text", text: opts.userText ?? "把第 8 集剩下的视频都生成完，然后帮我审一遍片" }],
  };
  useAssistantStore.setState({ turns: [...s.turns, userTurn], sessionStatus: "running", draftTurn: null });

  const draft: Turn = { type: "assistant", uuid: "", timestamp: nowIso(), content: [] };
  const emit = () => useAssistantStore.setState({ draftTurn: { ...draft, content: [...draft.content] } });
  let t = 300;
  const at = (ms: number, fn: () => void) => {
    t += ms;
    timers.push(window.setTimeout(fn, t));
  };

  // 1. 思考（流式）
  at(0, () => {
    draft.content.push({ type: "thinking", thinking: "" });
    emit();
  });
  const thinking =
    "用户要把第 8 集剩下的视频都生成完。先看工作流计划，确认哪些单元缺视频、哪些依据已经过期；U12 依赖 U11 的尾帧，需要排在最后。生成完再派两个审片子代理分前后两半检查。";
  for (let i = 0; i < thinking.length; i += 6) {
    at(40, () => {
      (draft.content[0]).thinking = thinking.slice(0, i + 6);
      emit();
    });
  }
  // 2. 工具：读工作流计划
  at(300, () => {
    draft.content.push({ type: "tool_use", id: uid("tool"), name: "mcp__arcreel__get_workflow_plan", input: { episode_id: 8 } });
    emit();
  });
  at(900, () => {
    draft.content[1] = { ...draft.content[1], result: '{"episode": 8, "missing_videos": ["E8U02", "…", "E8U12"]}' };
    emit();
  });
  // 3. 待办
  at(300, () => {
    draft.content.push({
      type: "tool_use",
      id: uid("todo"),
      name: "TodoWrite",
      input: { todos: TODOS_RUNNING },
      result: "Todos have been modified successfully.",
    });
    emit();
  });
  // 4. 工具：生成视频（运行中一段时间）
  at(300, () => {
    draft.content.push({
      type: "tool_use",
      id: uid("tool"),
      name: "mcp__arcreel__generate_videos",
      input: { script: "episode_8.json", target: { scope: "episode", episode_id: 8 } },
    });
    emit();
  });
  at(1400, () => {
    draft.content[3] = { ...draft.content[3], result: '{"queued": 10, "estimated_cost": 18.4}' };
    draft.content.push({ type: "text", text: "" });
    emit();
  });
  // 5. 正文逐字增长（同一轮内的增长，考验跟随）
  for (let i = 0; i < STREAM_TEXT.length; i += 7) {
    at(28, () => {
      (draft.content[4]).text = STREAM_TEXT.slice(0, i + 7);
      emit();
    });
  }
  // 6. 收尾：草稿落为正式 turn
  at(400, () => {
    const st = useAssistantStore.getState();
    const final: Turn = { ...draft, uuid: uid("asst"), content: [...draft.content] };
    useAssistantStore.setState({ turns: [...st.turns, final], draftTurn: null, sessionStatus: "completed" });
  });
}

export function stopStreaming() {
  clearTimers();
  const st = useAssistantStore.getState();
  if (!st.draftTurn) return;
  const partial: Turn = { ...st.draftTurn, uuid: uid("asst") };
  useAssistantStore.setState({
    turns: [...st.turns, partial, { type: "system", uuid: uid("sys"), content: [{ type: "interrupt_notice" }] }],
    draftTurn: null,
    sessionStatus: "interrupted",
  });
}

// ---------------------------------------------------------------------------
// 提问
// ---------------------------------------------------------------------------

export const PROTO_QUESTION: PendingQuestion = {
  question_id: "proto-question",
  questions: [
    {
      header: "U12 参考",
      question: "U12 是结尾的反打镜头，要用哪张图作为首帧参考？",
      multiSelect: false,
      options: [
        { label: "U11 的尾帧", description: "画面最连贯，但要等 U11 生成完才能开始，整集多等约 2 分钟。" },
        { label: "场景资产「豪华酒店总统大厅」", description: "可以马上开始，和 U11 之间可能有光线跳变。" },
        { label: "不用参考图", description: "只按提示词生成，构图最自由，风格一致性最差。" },
      ],
    },
    {
      header: "审片范围",
      question: "生成完以后，哪些方面需要审片子代理重点检查？",
      multiSelect: true,
      options: [
        { label: "人物一致性", description: "滑膛和朱汉杨的脸、服装是否和角色资产一致。" },
        { label: "口型与台词", description: "台词较长的 U09、U10 是否对得上口型。" },
        { label: "运镜稳定性", description: "U07 的长时间上摇是否抖动。" },
      ],
    },
  ],
};

export function askQuestion() {
  keepSnapshot();
  appendTurns({
    type: "assistant",
    uuid: uid("asst"),
    timestamp: nowIso(),
    content: [
      { type: "text", text: "开始生成前有两件事需要你定一下。" },
      {
        type: "tool_use",
        id: uid("ask"),
        name: "AskUserQuestion",
        input: { questions: PROTO_QUESTION.questions },
      },
    ],
  });
  useAssistantStore.setState({ pendingQuestion: PROTO_QUESTION, sessionStatus: "running" });
}

/** 原型内回答：落一条问答回执，不调用后端。 */
export function answerProtoQuestion(answers: Record<string, string>) {
  const st = useAssistantStore.getState();
  useAssistantStore.setState({
    pendingQuestion: null,
    sessionStatus: "completed",
    turns: [
      ...st.turns,
      { type: "user", uuid: uid("qa"), timestamp: nowIso(), content: [{ type: "question_answer", answers }] },
      {
        type: "assistant",
        uuid: uid("asst"),
        timestamp: nowIso(),
        content: [{ type: "text", text: "好的，按你的选择排进队列了。" }],
      },
    ],
  });
}

// ---------------------------------------------------------------------------
// 其他样本
// ---------------------------------------------------------------------------

export function addTodos() {
  appendTurns({
    type: "assistant",
    uuid: uid("asst"),
    timestamp: nowIso(),
    content: [
      { type: "text", text: "我把这一轮拆成五步，按顺序做。" },
      { type: "tool_use", id: uid("todo"), name: "TodoWrite", input: { todos: TODOS_RUNNING }, result: "ok" },
    ],
  });
}

const FAILURE: FailureObservation = {
  version: 1,
  phase: "turn",
  timestamp: nowIso(),
  project_name: "proj-b961d00c",
  session_id: "bb6f217f-61b8-43cb-9f14-e665d4b35f0e",
  summary: {
    source: "anthropic_api",
    type: "overloaded_error",
    status: 529,
    message: "Overloaded. The upstream model is temporarily unavailable, please retry later.",
  },
  raw: { type: "error", error: { type: "overloaded_error", message: "Overloaded" }, request_id: "req_011CZx9…" },
};

export function addFailure() {
  appendTurns({ type: "system", uuid: uid("fail"), timestamp: nowIso(), content: [{ type: "agent_failure", failure: FAILURE }] });
}

export function addToolError() {
  appendTurns({
    type: "assistant",
    uuid: uid("asst"),
    timestamp: nowIso(),
    content: [
      {
        type: "tool_use",
        id: uid("tool"),
        name: "mcp__arcreel__generate_videos",
        input: { script: "episode_8.json", target: { scope: "selected", ids: ["E8U01", "E8U11"] } },
        is_error: true,
        result: 'provider custom-2 returned 404 {"code":"InvalidURL","message":"Invalid URL"}',
      },
      { type: "text", text: "两段都没有生成出来，供应商返回 404「Invalid URL」，没有扣费。" },
    ],
  });
}

function sampleImage(): { data: string; media_type: string } {
  const c = document.createElement("canvas");
  c.width = 320;
  c.height = 180;
  const g = c.getContext("2d")!;
  const grad = g.createLinearGradient(0, 0, 320, 180);
  grad.addColorStop(0, "#2b2340");
  grad.addColorStop(1, "#7a5c3a");
  g.fillStyle = grad;
  g.fillRect(0, 0, 320, 180);
  g.fillStyle = "#f3e2c0";
  g.font = "20px sans-serif";
  g.fillText("参考：总统大厅黄昏", 24, 96);
  const url = c.toDataURL("image/png");
  return { data: url.split(",")[1], media_type: "image/png" };
}

export function addImageMessage() {
  const img = sampleImage();
  appendTurns({
    type: "user",
    uuid: uid("user"),
    timestamp: nowIso(),
    content: [
      { type: "image", source: { type: "base64", ...img } },
      { type: "text", text: "U01 的光线照这张图调，暖一点" },
    ],
  });
}

export function addLongCode() {
  appendTurns({
    type: "assistant",
    uuid: uid("asst"),
    timestamp: nowIso(),
    content: [
      {
        type: "text",
        text:
          "U07 的视频提示词我改成了下面这样，长句拆开了，运镜写得更具体：\n\n```text\n夜空，南半球银河横贯画面，镜头从地平线上的酒店天台缓慢上摇至银河中心，持续 12 秒，匀速，无抖动；朱汉杨背影位于画面左下三分之一，仰望，风吹动衣摆；冷色调，星点清晰，不出现月亮。\n```\n\n| 单元 | 时长 | 参考图 |\n| --- | --- | --- |\n| E8U07 | 12s | 南半球天空 |\n| E8U08 | 8s | 南半球天空 |\n| E8U09 | 10s | 豪华酒店总统大厅 |",
      },
    ],
  });
}

export function setContext() {
  useAppStore.getState().setFocusedContext({ type: "character", id: "滑膛" });
}
