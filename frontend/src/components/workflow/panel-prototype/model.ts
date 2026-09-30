// PROTOTYPE（#2828，一次性代码，勿合入 main）
// WorkflowPanel 新形态的场景数据：每个场景是一集的「内容现状 + 建议的下一步」，全是内存假数据。
// 建议顺序取自 #2668 决议第 10 条；草稿修法取自 #2659；资产图取自 #2656；广告取自 #2661。

export type ContentMode = "narration" | "drama" | "ad";
export type GenMode = "storyboard" | "grid" | "reference";
export type Tone = "done" | "todo" | "partial" | "running" | "warn" | "danger";

/** agent = 交给 Agent（主）；ai = 直接 AI 调用（次）；nav = 跳到某处手动做；danger = 需确认的破坏性操作。 */
export interface Act {
  label: string;
  kind: "agent" | "ai" | "nav" | "danger";
  /** 点下去之后会发生什么（原型里只在右下角回显）。 */
  effect?: string;
  /** 不可点时悬停显示的原因，统一以「需要先……」开头。 */
  disabled?: string;
}

export interface Note {
  text: string;
  tone: "warn" | "danger" | "info";
  act?: Act;
}

export interface Row {
  key: string;
  title: string;
  tone: Tone;
  /** 内容现状，一句话。 */
  status: string;
  /** 用于紧凑形态（横条）的短状态。 */
  short: string;
  notes?: Note[];
  /** 这一行上常驻的入口（不一定是建议的下一步）。 */
  acts?: Act[];
}

export interface NextStep {
  rowKey: string;
  title: string;
  detail: string;
  /** 本集保存的附加指令，重新生成时预填；undefined 表示此动作不带附加指令。 */
  instruction?: string;
  primary: Act[];
  alternatives?: Act[];
  /** 下一步本身的提示（如准入不满足时的引导）。 */
  hint?: Note;
}

export interface Scenario {
  id: string;
  label: string;
  content: ContentMode;
  gen: GenMode;
  episode: string;
  /** 收起时的一句话现状。 */
  summary: string;
  rows: Row[];
  next: NextStep | null;
  ad?: { total?: number; target: number };
}

export const CONTENT_LABEL: Record<ContentMode, string> = {
  narration: "旁白解说",
  drama: "剧情演绎",
  ad: "广告/短片",
};
export const GEN_LABEL: Record<GenMode, string> = {
  storyboard: "分镜图生视频",
  grid: "宫格生视频",
  reference: "参考生视频",
};

const agent = (effect: string, label = "交给 Agent"): Act => ({ label, kind: "agent", effect: `预填 Agent 输入框：「${effect}」` });
const ai = (label: string, effect?: string): Act => ({ label, kind: "ai", effect: effect ?? `打开「${label}」确认框（范围 + 附加指令）` });
const nav = (label: string, effect?: string): Act => ({ label, kind: "nav", effect: effect ?? `跳到：${label}` });

// ---- 行的构造：只写与默认不同的部分 --------------------------------------------------

const unitWord = (gen: GenMode) => (gen === "reference" ? "视频单元" : "分镜");

function source(tone: Tone, status: string, short: string, extra: Partial<Row> = {}): Row {
  return { key: "source", title: "集原文", tone, status, short, ...extra };
}
function plan(tone: Tone, status: string, short: string, extra: Partial<Row> = {}): Row {
  return { key: "plan", title: "脚本规划", tone, status, short, ...extra };
}
function script(tone: Tone, status: string, short: string, extra: Partial<Row> = {}): Row {
  return { key: "script", title: "正式脚本", tone, status, short, ...extra };
}
function prompts(tone: Tone, status: string, short: string, extra: Partial<Row> = {}): Row {
  return { key: "prompts", title: "提示词", tone, status, short, ...extra };
}
function assets(tone: Tone, status: string, short: string, extra: Partial<Row> = {}): Row {
  return { key: "assets", title: "资产图", tone, status, short, ...extra };
}
function boards(gen: GenMode, tone: Tone, status: string, short: string, extra: Partial<Row> = {}): Row {
  return { key: "boards", title: gen === "grid" ? "分镜图 / 宫格" : "分镜图", tone, status, short, ...extra };
}
function voice(status: string, short: string): Row {
  return { key: "voice", title: "旁白配音", tone: "done", status, short, acts: [nav("改为使用 TTS")] };
}
function videos(tone: Tone, status: string, short: string, extra: Partial<Row> = {}): Row {
  return { key: "videos", title: "视频", tone, status, short, ...extra };
}
function edit(tone: Tone, status: string, short: string): Row {
  return {
    key: "edit",
    title: "剪辑",
    tone,
    status,
    short,
    notes: [{ tone: "info", text: "「剪辑」一步的最终形态不在本票，此行仅占位。" }],
  };
}
function brief(tone: Tone, status: string, short: string, extra: Partial<Row> = {}): Row {
  return { key: "brief", title: "创作灵感与商品", tone, status, short, ...extra };
}

/** 前期未产生脚本时，后续各行的统一陈述：只写「还没有」，不写「等待上一步」。 */
function emptyTail(gen: GenMode, content: ContentMode, assetStatus: string): Row[] {
  const rows: Row[] = [
    prompts("todo", "还没有", "—"),
    assets("partial", assetStatus, "已登记"),
  ];
  if (gen !== "reference") rows.push(boards(gen, "todo", "还没有", "—"));
  if (content === "narration") rows.push(voice("后期配音（成片不含旁白音轨）", "后期配音"));
  rows.push(videos("todo", "还没有", "—"), edit("todo", "还没有视频可剪辑", "—"));
  return rows;
}

// ---- 场景 --------------------------------------------------------------------------

export const SCENARIOS: Scenario[] = [
  {
    id: "n-sb-source",
    label: "有集原文，还没有脚本",
    content: "narration",
    gen: "storyboard",
    episode: "第 4 集 · 雨夜来客",
    summary: "集原文 4,820 字 · 还没有脚本",
    rows: [
      source("done", "从整本源文切出 · 4,820 字", "4,820 字"),
      plan("todo", "还没有", "—", { acts: [ai("AI 规划脚本")] }),
      script("todo", "还没有", "—"),
      ...emptyTail("storyboard", "narration", "项目已登记 6 个资产 · 本集引用在规划后确定"),
    ],
    next: {
      rowKey: "plan",
      title: "AI 规划脚本",
      detail: "按本集原文规划分镜，同时找出本集新出现的角色、场景和道具，确认时一起登记。",
      instruction: "旁白保持第一人称，开头 3 秒要有悬念",
      primary: [agent("为第 4 集规划脚本"), ai("AI 规划脚本")],
      alternatives: [nav("从空白开始", "在本集建一份空的正式脚本，进入时间线手写")],
    },
  },
  {
    id: "d-sb-nosource",
    label: "手工新建的集，没有原文",
    content: "drama",
    gen: "storyboard",
    episode: "番外 · 少年时",
    summary: "没有集原文 · 还没有脚本",
    rows: [
      source("todo", "没有原文（手工新建的集）", "无原文", { acts: [nav("补充集原文")] }),
      plan("todo", "还没有", "—", {
        acts: [{ label: "AI 规划脚本", kind: "ai", disabled: "需要先补充集原文" }],
      }),
      script("todo", "还没有", "—"),
      ...emptyTail("storyboard", "drama", "项目已登记 11 个资产"),
    ],
    next: {
      rowKey: "script",
      title: "从空白开始",
      detail: "建一份空的正式脚本，进入时间线逐个添加分镜。",
      primary: [nav("从空白开始", "建出空的正式脚本并进入时间线")],
      alternatives: [nav("补充集原文", "打开集原文编辑：上传或粘贴")],
    },
  },
  {
    id: "d-ref-confirm",
    label: "脚本规划待确认",
    content: "drama",
    gen: "reference",
    episode: "第 7 集 · 断桥",
    summary: "脚本规划待确认 · 14 个视频单元 · 本集新增 3 个资产",
    rows: [
      source("done", "从整本源文切出 · 5,310 字", "5,310 字"),
      plan("partial", "已规划 14 个视频单元 · 待确认 · 本集新增 3 个资产（2 个角色、1 个场景）", "待确认", {
        acts: [nav("重新规划", "覆盖确认后重新规划（附加指令预填）")],
      }),
      script("todo", "还没有（确认后生成）", "—"),
      prompts("todo", "还没有", "—"),
      assets("partial", "本集引用 9 个 · 其中 3 个确认后登记", "9 个"),
      videos("todo", "还没有", "—"),
      edit("todo", "还没有视频可剪辑", "—"),
    ],
    next: {
      rowKey: "plan",
      title: "内容确认",
      detail: "核对规划，决定 3 个新增资产怎么登记（采用 / 归到已有 / 作为衍生 / 不登记），确认后转为正式脚本。",
      primary: [nav("去确认", "打开脚本规划的内容确认页")],
    },
  },
  {
    id: "d-sb-repair",
    label: "待修复草稿",
    content: "drama",
    gen: "storyboard",
    episode: "第 2 集 · 入京",
    summary: "脚本规划草稿有 4 处不符合要求",
    rows: [
      source("done", "从整本源文切出 · 4,105 字", "4,105 字"),
      plan("warn", "待修复草稿 · 4 处不符合要求（2 个分镜时长不在档位内、1 个引用了未登记的道具、整集缺钩子）", "待修复", {
        notes: [{ tone: "info", text: "修改后每次保存都会重新检查，全部符合要求时自动采用。" }],
      }),
      script("todo", "还没有", "—"),
      ...emptyTail("storyboard", "drama", "项目已登记 8 个资产"),
    ],
    next: {
      rowKey: "plan",
      title: "修复脚本规划草稿",
      detail: "AI 产出有 4 处不符合要求。可以只让 AI 改这几处，也可以自己改。",
      instruction: "",
      primary: [agent("修复第 2 集的脚本规划草稿"), ai("AI 修复", "只改 4 处违约所在的条目，其余原样保留")],
      alternatives: [
        nav("手动修改", "打开草稿，违约挂在对应条目上"),
        ai("重新生成", "确认「当前待修复草稿（含你的修改）将被替换」后重新规划"),
        { label: "丢弃草稿", kind: "danger", effect: "确认：丢弃后本集回到「尚无脚本规划」" },
      ],
    },
  },
  {
    id: "n-sb-agentdraft",
    label: "Agent 有未完成的修改",
    content: "narration",
    gen: "storyboard",
    episode: "第 5 集 · 旧信",
    summary: "Agent 有一份未完成的修改 · 内容确认暂停",
    rows: [
      source("done", "从整本源文切出 · 3,960 字", "3,960 字"),
      plan("warn", "Agent 有一份未完成的修改 · 采用或丢弃之前，内容确认暂停，已有规划只读", "Agent 修改中"),
      script("todo", "还没有", "—"),
      ...emptyTail("storyboard", "narration", "项目已登记 6 个资产"),
    ],
    next: {
      rowKey: "plan",
      title: "处理 Agent 未完成的修改",
      detail: "这份修改只能由 Agent 完成；也可以丢弃，回到已有的脚本规划。",
      primary: [agent("继续完成第 5 集脚本规划的修改", "交给 Agent 完成")],
      alternatives: [{ label: "丢弃这份修改", kind: "danger", effect: "确认：丢弃后回到已有的脚本规划（18 个分镜）" }],
    },
  },
  {
    id: "d-sb-stale",
    label: "集原文变了（脚本依据过期）",
    content: "drama",
    gen: "storyboard",
    episode: "第 3 集 · 夜审",
    summary: "原文已重新规划 · 现有脚本依据的是旧原文",
    rows: [
      source("warn", "原文已重新规划 · 比脚本依据的原文多 620 字", "已变", { acts: [nav("查看原文变化")] }),
      plan("done", "已确认", "已确认"),
      script("done", "18 个分镜 · 2 分 40 秒", "18 个"),
      prompts("done", "18 个都已编写", "齐"),
      assets("done", "本集引用 7 个 · 都有图", "齐"),
      boards("storyboard", "done", "18 个都有图", "齐"),
      videos("partial", "18 个分镜 · 12 个有视频", "12/18"),
      edit("todo", "还有 6 个分镜没有视频", "—"),
    ],
    next: {
      rowKey: "script",
      title: "重新规划本集脚本",
      detail: "按新的集原文重新规划。执行前会列出会丢失的内容（手改过的分镜文本等）；已生成的图和视频进入版本历史。",
      instruction: "保留第 1–6 个分镜的节奏",
      primary: [agent("按新原文重新规划第 3 集脚本"), ai("AI 重新规划")],
      alternatives: [nav("手动对照原文修改", "左右对照打开新旧原文与时间线")],
    },
  },
  {
    id: "d-sb-empty",
    label: "从空白开始，还没有分镜",
    content: "drama",
    gen: "storyboard",
    episode: "番外 · 少年时",
    summary: "正式脚本是空的",
    rows: [
      source("todo", "没有原文（手工新建的集）", "无原文", { acts: [nav("补充集原文")] }),
      plan("todo", "没有（从空白开始）", "—"),
      script("todo", "0 个分镜 · 从空白开始", "0 个"),
      prompts("todo", "还没有", "—"),
      assets("partial", "项目已登记 11 个资产", "已登记"),
      boards("storyboard", "todo", "还没有", "—"),
      videos("todo", "还没有", "—"),
      edit("todo", "还没有视频可剪辑", "—"),
    ],
    next: {
      rowKey: "script",
      title: "添加分镜",
      detail: "在时间线里添加第一个分镜。新分镜会标为待编写，写完文本后可以一键编写提示词。",
      primary: [nav("添加分镜", "在时间线末尾追加一个空分镜")],
      alternatives: [nav("补充集原文", "补上原文后可以交给 AI 规划整集（覆盖前确认）")],
    },
  },
  {
    id: "n-sb-prompts",
    label: "有待编写的提示词",
    content: "narration",
    gen: "storyboard",
    episode: "第 4 集 · 雨夜来客",
    summary: "24 个分镜 · 9 个待编写",
    rows: [
      source("done", "从整本源文切出 · 4,820 字", "4,820 字"),
      plan("done", "已确认", "已确认"),
      script("done", "24 个分镜 · 3 分 12 秒", "24 个"),
      prompts("partial", "24 个分镜 · 9 个待编写（含 2 个手动新增的）", "9 待编写"),
      assets("partial", "本集引用 7 个 · 2 个待生成", "2 待生成"),
      boards("storyboard", "todo", "24 个分镜 · 都还没有图", "0/24"),
      voice("后期配音（成片不含旁白音轨）", "后期配音"),
      videos("todo", "还没有", "—"),
      edit("todo", "还没有视频可剪辑", "—"),
    ],
    next: {
      rowKey: "prompts",
      title: "编写提示词",
      detail: "为 9 个待编写的分镜写图片和视频提示词，已写好的提示词会保留。",
      instruction: "镜头语言偏纪录片，少用特写",
      primary: [agent("为第 4 集的 9 个待编写分镜编写提示词"), ai("AI 编写")],
      alternatives: [nav("在时间线手动编写")],
    },
  },
  {
    id: "d-ref-replan",
    label: "有需要修改的单元",
    content: "drama",
    gen: "reference",
    episode: "第 7 集 · 断桥",
    summary: "14 个视频单元 · 2 个需要修改",
    rows: [
      source("done", "从整本源文切出 · 5,310 字", "5,310 字"),
      plan("done", "已确认", "已确认"),
      script("done", "14 个视频单元 · 2 分 05 秒", "14 个"),
      prompts("warn", "14 个都已编写 · 2 个需要修改（U5、U9 超出当前视频模型的时长上限）", "2 需修改", {
        notes: [{ tone: "warn", text: "U5 要 18 秒，当前模型最长 15 秒；U9 同。", act: nav("查看 U5") }],
      }),
      assets("done", "本集引用 9 个 · 都有图", "齐"),
      videos("todo", "还没有", "0/14"),
      edit("todo", "还没有视频可剪辑", "—"),
    ],
    next: {
      rowKey: "prompts",
      title: "修改这些单元",
      detail: "U5、U9 超出当前视频模型的时长上限：拆短、缩短时长或换模型。其余 12 个单元可以先生成视频。",
      primary: [agent("修改第 7 集的 U5、U9，使时长不超过 15 秒"), nav("去修改 U5")],
      alternatives: [ai("先为其余 12 个生成视频", "打开视频批量确认框（12 个单元，预估费用）")],
    },
  },
  {
    id: "d-sb-assets",
    label: "本集资产图待生成",
    content: "drama",
    gen: "storyboard",
    episode: "第 2 集 · 入京",
    summary: "本集引用 8 个资产 · 3 个待生成",
    rows: [
      source("done", "从整本源文切出 · 4,105 字", "4,105 字"),
      plan("done", "已确认", "已确认"),
      script("done", "20 个分镜 · 2 分 50 秒", "20 个"),
      prompts("done", "20 个都已编写", "齐"),
      assets("partial", "本集引用 8 个 · 3 个待生成 · 1 个过期", "3 待生成", {
        notes: [
          { tone: "warn", text: "1 个过期：林默（描述改过）", act: nav("在画廊查看", "跳到画廊并筛出过期") },
          { tone: "info", text: "另有 1 个缺描述，不能生成：旧宅后院", act: nav("去填写描述") },
        ],
      }),
      boards("storyboard", "todo", "20 个分镜 · 都还没有图", "0/20"),
      videos("todo", "还没有", "—"),
      edit("todo", "还没有视频可剪辑", "—"),
    ],
    next: {
      rowKey: "assets",
      title: "生成资产图",
      detail: "本集引用的 3 个资产还没有图：林默（少年）、青铜匣、祠堂。衍生会在本体完成后自动生成。",
      primary: [agent("为第 2 集引用的待生成资产生成资产图"), ai("生成 3 张资产图", "打开批量确认框：名单、跳过项、预估 ¥0.60")],
      alternatives: [nav("先生成分镜图", "缺图资产的分镜会被跳过")],
    },
  },
  {
    id: "n-grid-boards",
    label: "宫格：分镜图缺",
    content: "narration",
    gen: "grid",
    episode: "第 6 集 · 渡口",
    summary: "24 个分镜 · 8 个缺分镜图",
    rows: [
      source("done", "自带原文 · 3,480 字", "3,480 字"),
      plan("done", "已确认", "已确认"),
      script("done", "24 个分镜 · 3 分 00 秒", "24 个"),
      prompts("done", "24 个都已编写", "齐"),
      assets("done", "本集引用 6 个 · 都有图", "齐"),
      boards("grid", "partial", "24 个分镜 · 16 个有图 · 8 个缺（2 组宫格）", "16/24"),
      voice("使用 TTS（配音随视频生成）", "TTS"),
      videos("partial", "16 个有视频", "16/24"),
      edit("todo", "还有 8 个分镜没有视频", "—"),
    ],
    next: {
      rowKey: "boards",
      title: "生成分镜图",
      detail: "8 个分镜缺图，分属 2 组宫格。可以整组生成宫格，也可以逐个生成分镜图。",
      primary: [agent("为第 6 集缺图的分镜生成宫格"), ai("生成 2 组宫格")],
      alternatives: [ai("逐个生成 8 张分镜图")],
    },
  },
  {
    id: "d-sb-videos",
    label: "视频生成中，部分分镜图过期",
    content: "drama",
    gen: "storyboard",
    episode: "第 1 集 · 初见",
    summary: "18 个分镜 · 7 个有视频 · 4 个生成中",
    rows: [
      source("done", "从整本源文切出 · 4,400 字", "4,400 字"),
      plan("done", "已确认", "已确认"),
      script("done", "18 个分镜 · 2 分 30 秒", "18 个"),
      prompts("done", "18 个都已编写", "齐"),
      assets("done", "本集引用 7 个 · 都有图", "齐"),
      boards("storyboard", "partial", "18 个都有图 · 2 张过期（引用的资产图更新了）", "2 过期", {
        acts: [nav("查看过期的 2 张")],
      }),
      videos("running", "18 个分镜 · 7 个有视频 · 4 个生成中 · 7 个还没有", "7/18"),
      edit("todo", "还有 11 个分镜没有视频", "—"),
    ],
    next: {
      rowKey: "videos",
      title: "生成视频",
      detail: "为还没有视频的 7 个分镜生成；生成中的 4 个不重复提交。过期的分镜图不会自动更新。",
      primary: [agent("为第 1 集还没有视频的分镜生成视频"), ai("生成 7 段视频", "打开批量确认框：7 个分镜、时长档位、预估 ¥12.60")],
    },
  },
  {
    id: "n-sb-done",
    label: "全部就绪",
    content: "narration",
    gen: "storyboard",
    episode: "第 4 集 · 雨夜来客",
    summary: "24 个分镜 · 视频全部就绪",
    rows: [
      source("done", "从整本源文切出 · 4,820 字", "4,820 字"),
      plan("done", "已确认", "已确认"),
      script("done", "24 个分镜 · 3 分 12 秒", "24 个"),
      prompts("done", "24 个都已编写", "齐"),
      assets("done", "本集引用 7 个 · 都有图 · 1 个过期", "齐", {
        notes: [{ tone: "warn", text: "1 个过期：青铜匣（描述改过）", act: nav("在画廊查看") }],
      }),
      boards("storyboard", "done", "24 个都有图", "齐"),
      voice("后期配音（成片不含旁白音轨）", "后期配音"),
      videos("done", "24 个都有视频", "齐"),
      edit("todo", "可以剪辑", "可剪辑"),
    ],
    next: {
      rowKey: "edit",
      title: "剪辑并导出",
      detail: "本集素材已齐。",
      primary: [nav("去剪辑")],
      alternatives: [nav("去第 5 集 · 旧信", "下一个未完成的集")],
    },
  },
  {
    id: "d-ref-promptdraft",
    label: "参考生视频：提示词编写草稿待修复",
    content: "drama",
    gen: "reference",
    episode: "第 8 集 · 回乡",
    summary: "提示词编写草稿有 2 处不符合要求",
    rows: [
      source("done", "从整本源文切出 · 4,950 字", "4,950 字"),
      plan("done", "已确认", "已确认"),
      script("done", "12 个视频单元 · 1 分 58 秒", "12 个"),
      prompts("warn", "待修复草稿 · 2 处不符合要求（U3 引用了未登记的角色、U8 镜头数超上限）", "待修复"),
      assets("done", "本集引用 8 个 · 都有图", "齐"),
      videos("todo", "还没有", "—"),
      edit("todo", "还没有视频可剪辑", "—"),
    ],
    next: {
      rowKey: "prompts",
      title: "修复提示词编写草稿",
      detail: "AI 写的提示词有 2 处不符合要求，修好之前正式脚本保持原样。",
      instruction: "",
      primary: [agent("修复第 8 集的提示词编写草稿"), ai("AI 修复", "只改 U3、U8")],
      alternatives: [
        nav("手动修改"),
        ai("重新生成"),
        { label: "丢弃草稿", kind: "danger", effect: "确认：丢弃后回到当前正式脚本（12 个单元待编写）" },
      ],
    },
  },
  {
    id: "ad-sb-nobrief",
    label: "广告：还没有灵感和商品",
    content: "ad",
    gen: "storyboard",
    episode: "夏日冰饮",
    summary: "还没有脚本 · 创作灵感为空",
    ad: { target: 30 },
    rows: [
      brief("todo", "创作灵感为空 · 没有商品 · 目标时长 30 秒", "未填写", {
        acts: [nav("填写创作灵感", "跳到项目概览的「创作灵感」区"), nav("添加商品")],
      }),
      script("todo", "还没有", "—"),
      prompts("todo", "还没有", "—"),
      assets("todo", "还没有", "—"),
      boards("storyboard", "todo", "还没有", "—"),
      videos("todo", "还没有", "—"),
      edit("todo", "还没有视频可剪辑", "—"),
    ],
    next: {
      rowKey: "script",
      title: "AI 生成脚本",
      detail: "按创作灵感与商品一次写出完整脚本（含提示词），并登记新出现的资产。",
      instruction: "",
      primary: [
        { label: "交给 Agent", kind: "agent", disabled: "需要先填写创作灵感或添加商品" },
        { label: "AI 生成脚本", kind: "ai", disabled: "需要先填写创作灵感或添加商品" },
      ],
      alternatives: [nav("从空白开始", "建出空的正式脚本并进入时间线")],
      hint: { tone: "info", text: "请先填写创作灵感或添加商品", act: nav("去填写", "跳到项目概览的「创作灵感」区") },
    },
  },
  {
    id: "ad-sb-ready",
    label: "广告：有灵感，还没有脚本",
    content: "ad",
    gen: "storyboard",
    episode: "夏日冰饮",
    summary: "还没有脚本",
    ad: { target: 30 },
    rows: [
      brief("done", "「海边午后，一口冰凉」· 2 个商品 · 目标时长 30 秒", "已填写", { acts: [nav("编辑创作灵感")] }),
      script("todo", "还没有", "—"),
      prompts("todo", "还没有", "—"),
      assets("partial", "已登记 2 个商品", "2 个"),
      boards("storyboard", "todo", "还没有", "—"),
      videos("todo", "还没有", "—"),
      edit("todo", "还没有视频可剪辑", "—"),
    ],
    next: {
      rowKey: "script",
      title: "AI 生成脚本",
      detail: "按创作灵感与商品一次写出完整脚本（含提示词），并登记新出现的资产。",
      instruction: "",
      primary: [agent("为这支广告生成脚本"), ai("AI 生成脚本")],
      alternatives: [nav("从空白开始", "建出空的正式脚本并进入时间线")],
    },
  },
  {
    id: "ad-ref-videos",
    label: "广告：总时长超目标，视频生成中",
    content: "ad",
    gen: "reference",
    episode: "夏日冰饮",
    summary: "9 个视频单元 · 3 个有视频",
    ad: { total: 38, target: 30 },
    rows: [
      brief("done", "「海边午后，一口冰凉」· 2 个商品 · 目标时长 30 秒", "已填写", { acts: [nav("编辑创作灵感")] }),
      script("warn", "9 个视频单元 · 总时长 38 秒，比目标长 8 秒", "38s/30s", {
        acts: [ai("重新生成脚本", "覆盖确认：列出将丢失的手改内容"), nav("调整时长")],
      }),
      prompts("done", "9 个都已编写", "齐"),
      assets("done", "本集引用 5 个 · 都有图", "齐"),
      videos("running", "9 个单元 · 3 个有视频 · 2 个生成中", "3/9"),
      edit("todo", "还有 6 个单元没有视频", "—"),
    ],
    next: {
      rowKey: "videos",
      title: "生成视频",
      detail: "为还没有视频的 4 个单元生成；生成中的 2 个不重复提交。",
      primary: [agent("为这支广告还没有视频的单元生成视频"), ai("生成 4 段视频", "打开批量确认框（预估 ¥7.20）")],
    },
  },
];

export const TONE_COLOR: Record<Tone, string> = {
  done: "var(--color-accent-2)",
  todo: "var(--color-text-4)",
  partial: "var(--color-text-2)",
  running: "var(--color-accent-2)",
  warn: "var(--color-warm)",
  danger: "var(--color-danger-2)",
};

export const NOTE_COLOR: Record<Note["tone"], string> = {
  warn: "var(--color-warm)",
  danger: "var(--color-danger-2)",
  info: "var(--color-text-3)",
};

export { unitWord };
