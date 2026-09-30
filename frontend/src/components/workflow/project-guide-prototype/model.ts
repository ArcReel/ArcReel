// PROTOTYPE（#2829，一次性代码，勿合入 main）
// 项目层「下一步」、顶栏中间位置与迁移修复重试的场景数据，全是内存假数据。
// 建议顺序取自 #2668 决议第 9、10 条；按钮层级与措辞沿用 #2828 决议。
import { create } from "zustand";
import type { Act } from "../panel-prototype/model";

export type EpState = "done" | "progress" | "todo" | "stale" | "repair";

export interface Ep {
  id: number;
  title: string;
  state: EpState;
  /** 本集建议的下一步（与集页面板收起行一致）。 */
  next?: string;
}

export interface ProjectNext {
  title: string;
  detail: string;
  /** 分集规划的附加指令：不写进项目，输入框保留上一次内容；undefined 表示不带附加指令。 */
  instruction?: string;
  primary: Act[];
  alternatives?: Act[];
  /** 跨集建议时指向的集。 */
  episode?: number;
}

export interface ProjectScenario {
  id: string;
  label: string;
  content: "narration" | "drama" | "ad";
  episodes: Ep[];
  /** 整本源文现状，一句话；null 表示没有整本源文。 */
  source: string | null;
  /** 分集规划现状，一句话。 */
  planning?: string;
  /** 分集规划进行中的百分比。 */
  planningProgress?: number;
  reminders?: { text: string; act?: Act }[];
  next: ProjectNext | null;
  migration?: { reason: string };
}

const agent = (effect: string, label = "交给 Agent"): Act => ({ label, kind: "agent", effect: `预填 Agent 输入框：「${effect}」` });
const ai = (label: string, effect?: string): Act => ({ label, kind: "ai", effect: effect ?? `打开「${label}」确认框` });
const nav = (label: string, effect?: string): Act => ({ label, kind: "nav", effect: effect ?? `跳到：${label}` });

const TITLES = ["雨夜来客", "旧账", "码头", "失踪的账房", "对质", "火起", "逃亡", "山寺", "重逢", "清算", "余烬", "归乡"];
function eps(n: number, states: (EpState | [EpState, string])[]): Ep[] {
  return Array.from({ length: n }, (_, i) => {
    const s = states[i] ?? "todo";
    const [state, next] = Array.isArray(s) ? s : [s, undefined];
    return { id: i + 1, title: TITLES[i % TITLES.length], state, next };
  });
}

const planAct = (label = "AI 分集规划") =>
  ai(label, "打开「AI 分集规划」确认框：规划到源文结尾，附加要求沿用上次输入");

export const PROJECT_SCENARIOS: ProjectScenario[] = [
  {
    id: "new-nosource",
    label: "刚建项目，没有源文",
    content: "drama",
    episodes: [],
    source: null,
    next: {
      title: "上传原文",
      detail: "上传整本小说或剧本后由 AI 规划分集；也可以逐集上传原文，或者不用原文直接新建一集手写。",
      primary: [ai("上传原文", "打开上传对话框（整本源文 / 逐集原文）")],
      alternatives: [nav("新建一集", "在「分集」视图末尾新建一集（无原文）")],
    },
  },
  {
    id: "new-source",
    label: "已上传整本源文，还没有集",
    content: "narration",
    episodes: [],
    source: "《长夜》· 38.2 万字 · 全部未切分",
    planning: "还没有规划",
    next: {
      title: "AI 分集规划",
      detail: "从头规划到源文结尾，完成后可在「分集」视图逐集调整。",
      instruction: "",
      primary: [agent("为整本源文规划分集"), planAct()],
      alternatives: [nav("手工切分", "进入「分集」视图的划范围模式"), nav("新建一集", "在「分集」视图末尾新建一集（无原文）")],
    },
  },
  {
    id: "planning",
    label: "AI 分集规划进行中",
    content: "narration",
    episodes: eps(4, []),
    source: "《长夜》· 38.2 万字 · 已切分 9.1 万字",
    planning: "AI 分集规划中 · 42%",
    planningProgress: 42,
    next: {
      title: "等待 AI 分集规划完成",
      detail: "已切出的 4 集可以先开始制作。",
      primary: [nav("查看规划进度", "跳到「分集」视图")],
      alternatives: [nav("先去第 1 集", "跳到第 1 集")],
    },
  },
  {
    id: "no-source-manual",
    label: "原创项目，手工建了 2 集",
    content: "drama",
    episodes: eps(2, [["todo", "从空白开始"], ["todo", "从空白开始"]]),
    source: null,
    next: {
      title: "继续第 1 集",
      detail: "第 1 集还没有脚本，下一步：从空白开始。",
      primary: [nav("去第 1 集", "跳到第 1 集（集页面板展开到「从空白开始」）")],
      alternatives: [nav("上传原文", "打开上传对话框")],
      episode: 1,
    },
  },
  {
    id: "in-progress",
    label: "10 集，3 集完成，第 4 集进行中",
    content: "narration",
    episodes: eps(10, ["done", "done", "stale", ["progress", "编写提示词"], ["todo", "AI 规划脚本"], "todo", "todo", "done", "todo", "stale"]),
    source: "《长夜》· 38.2 万字 · 已切分 26.3 万字",
    planning: "已规划 10 集，源文还有约 11.9 万字未切分",
    reminders: [{ text: "2 集有产物需要更新（第 3、10 集）", act: nav("查看", "跳到第 3 集的过期行") }],
    next: {
      title: "继续第 4 集",
      detail: "第 4 集有 9 个分镜待编写提示词。",
      primary: [nav("去第 4 集", "跳到第 4 集（集页面板已指向「编写提示词」）")],
      episode: 4,
    },
  },
  {
    id: "all-done-remaining",
    label: "已规划的集全部完成，源文还有剩余",
    content: "drama",
    episodes: eps(10, Array<EpState>(10).fill("done")),
    source: "《长夜》· 38.2 万字 · 已切分 26.3 万字",
    planning: "源文还有约 11.9 万字未切分",
    next: {
      title: "继续 AI 分集规划",
      detail: "从第 10 集结尾接着规划到源文结尾。",
      instruction: "每集控制在 90 秒左右",
      primary: [agent("继续为剩余源文规划分集"), planAct("继续 AI 分集规划")],
      alternatives: [nav("去剪辑", "（剪辑入口形态归地图「导出与剪辑时间线」）")],
    },
  },
  {
    id: "all-done",
    label: "全部完成，源文已全部切分",
    content: "drama",
    episodes: eps(12, Array<EpState>(12).fill("done")),
    source: "《长夜》· 38.2 万字 · 已全部切分",
    planning: "已规划 12 集",
    next: {
      title: "剪辑与导出",
      detail: "全部 12 集都可以剪辑导出了。（最终形态归「导出与剪辑时间线」）",
      primary: [nav("去剪辑", "（占位）")],
    },
  },
  {
    id: "ad",
    label: "广告/短片，新建",
    content: "ad",
    episodes: eps(1, [["todo", "填写创作灵感与商品"]]),
    source: null,
    next: {
      title: "填写创作灵感与商品",
      detail: "短片只有一集，后续步骤都在短片页面。",
      primary: [nav("去填写", "跳到短片页（面板第一行「创作灵感与商品」）")],
      episode: 1,
    },
  },
  {
    id: "migration",
    label: "数据升级失败",
    content: "narration",
    episodes: eps(10, ["done", "done", "done", "progress"]),
    source: "《长夜》· 38.2 万字 · 已切分 26.3 万字",
    migration: { reason: "step 0014_episode_id: scripts/episode_4.json 读取失败：Expecting ',' delimiter: line 212 column 7" },
    next: null,
  },
];

// ---- 迁移重试：原型内存状态 ------------------------------------------------------------

export type RetryPhase = "idle" | "running" | "failed" | "succeeded";

interface RetryState {
  phase: RetryPhase;
  attempts: number;
  lastError: string | null;
  /** 下一次重试是否失败（左下角可切换）。 */
  willFail: boolean;
  setWillFail: (v: boolean) => void;
  retry: () => void;
  reset: () => void;
}

export const useRetry = create<RetryState>((set, get) => ({
  phase: "idle",
  attempts: 0,
  lastError: null,
  willFail: true,
  setWillFail: (willFail) => set({ willFail }),
  retry: () => {
    if (get().phase === "running") return;
    set({ phase: "running" });
    setTimeout(() => {
      const fail = get().willFail;
      set((s) => ({
        phase: fail ? "failed" : "succeeded",
        attempts: s.attempts + 1,
        lastError: fail
          ? "step 0014_episode_id: scripts/episode_4.json 读取失败：Expecting ',' delimiter: line 212 column 7"
          : null,
      }));
    }, 1600);
  },
  reset: () => set({ phase: "idle", attempts: 0, lastError: null }),
}));

export const retryAgentAct: Act = {
  label: "交给 Agent 排查",
  kind: "agent",
  effect: "预填 Agent 输入框：「项目数据升级失败，请排查原因并修复后重试」",
};

export function doneCount(p: ProjectScenario) {
  return p.episodes.filter((e) => e.state === "done").length;
}

export const EP_COLOR: Record<EpState, string> = {
  done: "var(--color-good)",
  progress: "var(--color-accent-2)",
  todo: "var(--color-text-4)",
  stale: "var(--color-warm)",
  repair: "var(--color-danger)",
};
export const EP_LABEL: Record<EpState, string> = {
  done: "已完成",
  progress: "制作中",
  todo: "未开始",
  stale: "需要更新",
  repair: "需要修复",
};
