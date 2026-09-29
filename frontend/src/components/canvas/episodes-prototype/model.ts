// PROTOTYPE（#2767，一次性代码，勿合入 main）：「分集」视图与划范围 UI 的内存数据模型。
// 位置一律以「句子下标」表示（切分点只落在句末），省掉字符偏移换算；窗口按句数缩小以便演示。

export type Origin = "cut" | "own" | "none";

export interface Ep {
  id: number;
  title: string;
  hook: string;
  origin: Origin;
  /** 切自整本源文的集：[起句, 止句)，止句不含 */
  range?: [number, number];
  ownChars?: number;
  ownFile?: string;
  hasArtifacts: boolean;
  /** 集规划状态 stale：「原文已重新规划」 */
  stale: boolean;
  fresh?: boolean;
}

export interface CandEp {
  title: string;
  hook: string;
  range: [number, number];
}

export interface Candidate {
  fromEp: number;
  instructions: string;
  eps: CandEp[];
  status: "generating" | "ready" | "stopped";
  /** 生成到的句位置 */
  reached: number;
}

export interface ProtoState {
  episodes: Ep[];
  cursor: number;
  planning: null | { mode: "toEnd" | "batch"; batchesDone: number };
  candidate: Candidate | null;
  lastInstructions: string;
  sourceReplaced: boolean;
  log: string[];
}

// ---------------------------------------------------------------- 源文

const CORPUS = [
  "雨下了整整一夜，码头上的灯一盏接一盏灭了。",
  "沈砚把领口竖起来，站在仓库门口没有动。",
  "他知道今晚来的人不会只有一个。",
  "“东西带来了吗？”黑暗里有人开口，嗓音沙哑。",
  "沈砚没有回答，只是把手里的信封往前递了半寸。",
  "对方笑了一声，那笑声让人想起生锈的铁门。",
  "许知意坐在车里，隔着起雾的玻璃盯着这一幕。",
  "她答应过父亲，绝不再踏进这座城半步。",
  "可父亲已经死了，承诺也就跟着埋进了土里。",
  "三年前那场火烧掉了许家的半条街，也烧掉了所有账本。",
  "所有人都说那是意外，只有她不信。",
  "沈砚回到车上时，衣角还在往下滴水。",
  "“他们要的不是钱。”他说，“他们要一个名字。”",
  "许知意转过头，第一次认真地看着这个男人。",
  "她想起老管家临终前说的那句话：别相信姓沈的。",
  "可眼下，她能信的人只剩他一个。",
  "第二天一早，报纸头版登出了仓库失火的消息。",
  "照片上的废墟里，隐约能看见半截烧焦的招牌。",
  "陆警官把报纸摔在桌上，烟灰落了一桌子。",
  "“又是许家的旧账。”他盯着窗外，声音压得很低。",
  "局里没人愿意碰这个案子，上一个碰的人已经调去了边境。",
  "许知意推门进来的时候，陆警官的烟刚好燃尽。",
  "“我要看三年前的卷宗。”她说得很平静。",
  "陆警官沉默了很久，最后从抽屉里摸出一把钥匙。",
  "档案室在地下二层，灯管坏了一半，走廊尽头一片漆黑。",
  "卷宗比她想象的薄得多，关键的几页被人撕掉了。",
  "撕口很整齐，像是用尺子比着裁下来的。",
  "她在最后一页的背面发现了一行铅笔字，字迹熟悉得让她发抖。",
  "那是父亲的笔迹，写的是一个日期和一个码头编号。",
  "沈砚看到那行字时，脸色第一次变了。",
  "“这个编号，”他慢慢说，“是我家的船。”",
  "窗外又开始下雨，雨点敲在铁皮屋檐上，像有人在数着什么。",
  "许知意把卷宗合上，指节因为用力而发白。",
  "她终于明白，这座城里没有一个人是干净的。",
  "包括她自己。",
  "夜里，有人往她住的旅馆门缝里塞了一张空白信封。",
  "信封里什么都没有，只有一股淡淡的烟草味。",
  "她认得那个味道，那是父亲生前最爱的牌子。",
  "沈砚说这是警告，她却觉得这是邀请。",
  "天亮之前，他们决定再去一次码头。",
];

const CHAPTER_NAMES = ["雨夜", "旧账", "卷宗", "空白信封", "船号", "火起", "证人", "黎明", "反咬", "摊牌"];
const CN_NUM = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"];

function rnd(n: number): number {
  const x = Math.sin(n * 12.9898 + 78.233) * 43758.5453;
  return x - Math.floor(x);
}

function buildSource(): { sentences: string[]; paraStart: boolean[]; chapter: (string | null)[] } {
  const sentences: string[] = [];
  const paraStart: boolean[] = [];
  const chapter: (string | null)[] = [];
  let chap = 0;
  const TOTAL = 420;
  for (let i = 0; i < TOTAL; i++) {
    if (i % 42 === 0) {
      const title = `第${CN_NUM[chap] ?? chap + 1}章 ${CHAPTER_NAMES[chap % CHAPTER_NAMES.length]}`;
      sentences.push(title);
      paraStart.push(true);
      chapter.push(title);
      chap++;
      continue;
    }
    sentences.push(CORPUS[(i * 7 + chap * 3) % CORPUS.length]);
    paraStart.push(rnd(i) < 0.3 || chapter[i - 1] !== null);
    chapter.push(null);
  }
  return { sentences, paraStart, chapter };
}

export const SOURCE = buildSource();
export const SOURCE_LEN = SOURCE.sentences.length;
/** 原型里的「窗口」，对应真实的 5 万字 */
export const WINDOW = 110;
const SOURCE_FILE_NAME = "雨夜码头.txt";
export { SOURCE_FILE_NAME };

export function charsOf(range: [number, number]): number {
  let n = 0;
  for (let i = range[0]; i < range[1]; i++) n += SOURCE.sentences[i].length;
  return n;
}

export const TOTAL_CHARS = charsOf([0, SOURCE_LEN]);

export function epChars(ep: Ep): number {
  if (ep.origin === "cut" && ep.range) return charsOf(ep.range);
  return ep.ownChars ?? 0;
}

/** 朗读量：按 250 字/分钟估 */
export function readLabel(chars: number): string {
  const min = chars / 250;
  return `${chars.toLocaleString()} 字 · 约 ${min < 1 ? "<1" : min.toFixed(1)} 分钟`;
}

export function firstSentence(ep: Ep | CandEp): string {
  if (!ep.range) return "";
  return SOURCE.sentences[ep.range[0]];
}
export function lastSentence(ep: Ep | CandEp): string {
  if (!ep.range) return "";
  return SOURCE.sentences[ep.range[1] - 1];
}

// ---------------------------------------------------------------- 假 AI

const TITLES = [
  "雨夜来客", "码头对峙", "她的条件", "空白信封", "逃不出的城", "第二次交易", "火起", "沉默的证人",
  "反咬", "摊牌", "黎明前", "旧船号", "撕掉的三页", "父亲的笔迹", "地下二层", "调去边境的人",
];
const HOOKS = [
  "信封递出去的那一刻，对方叫出了她父亲的名字。",
  "卷宗最后一页的铅笔字，指向一艘早已沉没的船。",
  "陆警官交出钥匙前，只提了一个条件。",
  "旅馆门缝里又多了一个信封，这次里面有东西。",
  "沈砚终于承认，三年前他就在码头上。",
  "火光里走出来的人，本该已经死了。",
];

/** 从 from 起读一个窗口，切出其中剧情弧完整的集；到源文结尾时收尾 */
export function fakePlanWindow(from: number, seed: number, titleOffset = 0): CandEp[] {
  const windowEnd = Math.min(from + WINDOW, SOURCE_LEN);
  const out: CandEp[] = [];
  let pos = from;
  let k = 0;
  while (true) {
    const len = 24 + Math.floor(rnd(seed * 31 + pos) * 14);
    const end = pos + len;
    if (end > windowEnd) {
      if (windowEnd === SOURCE_LEN && pos < SOURCE_LEN) {
        if (SOURCE_LEN - pos < 12 && out.length > 0) out[out.length - 1].range[1] = SOURCE_LEN;
        else out.push(mkCand(pos, SOURCE_LEN, titleOffset + k));
      }
      break;
    }
    out.push(mkCand(pos, end, titleOffset + k));
    pos = end;
    k++;
  }
  return out;
}

function mkCand(a: number, b: number, k: number): CandEp {
  return {
    title: TITLES[(a + k) % TITLES.length],
    hook: HOOKS[(a * 3 + k) % HOOKS.length],
    range: [a, b],
  };
}

// ---------------------------------------------------------------- 账本查询

export function cutEps(s: ProtoState): Ep[] {
  return s.episodes.filter((e) => e.origin === "cut").sort((a, b) => a.range![0] - b.range![0]);
}
export function otherEps(s: ProtoState): Ep[] {
  return s.episodes.filter((e) => e.origin !== "cut").sort((a, b) => a.id - b.id);
}
export function byId(s: ProtoState): Ep[] {
  return [...s.episodes].sort((a, b) => a.id - b.id);
}
export function maxId(s: ProtoState): number {
  return s.episodes.reduce((m, e) => Math.max(m, e.id), 0);
}
export function nextCut(s: ProtoState, id: number): Ep | undefined {
  const list = cutEps(s);
  const i = list.findIndex((e) => e.id === id);
  return i >= 0 ? list[i + 1] : undefined;
}
/** 拆分 / 合并只在该集及其后切出的集都没有产物时允许 */
export function canRestructure(s: ProtoState, id: number): boolean {
  const list = cutEps(s);
  const i = list.findIndex((e) => e.id === id);
  return i >= 0 && list.slice(i).every((e) => !e.hasArtifacts);
}
/** 从第 N 集（N>1 的非首个切出集）起重新规划时，范围内夹着其他来源的集就拒绝 */
export function replanBlockers(s: ProtoState, id: number): Ep[] {
  const first = cutEps(s)[0];
  if (!first || first.id === id) return [];
  return otherEps(s).filter((e) => e.id > id);
}

// ---------------------------------------------------------------- 初始账本

export function initialState(): ProtoState {
  const planned = fakePlanWindow(0, 1).concat(fakePlanWindow(fakePlanWindow(0, 1).at(-1)!.range[1], 2, 5));
  const first6 = planned.slice(0, 6);
  const episodes: Ep[] = first6.map((c, i) => ({
    id: i + 1,
    title: c.title,
    hook: c.hook,
    origin: "cut",
    range: c.range,
    hasArtifacts: i < 4,
    stale: i === 3,
  }));
  episodes.push({
    id: 7, title: "番外·雨夜", hook: "", origin: "own", ownChars: 2140, ownFile: "番外_雨夜.txt",
    hasArtifacts: true, stale: false,
  });
  episodes.push({ id: 8, title: "第 8 集", hook: "", origin: "none", hasArtifacts: false, stale: false });
  return {
    episodes,
    cursor: first6.at(-1)!.range[1],
    planning: null,
    candidate: null,
    lastInstructions: "每集结尾停在人物做出决定之前",
    sourceReplaced: false,
    log: ["初始账本：第 1–6 集切自整本源文（1–4 有产物，4 已 stale），第 7 集自带原文，第 8 集无原文"],
  };
}

// ---------------------------------------------------------------- 写操作（纯函数）

function withLog(s: ProtoState, msg: string): ProtoState {
  return { ...s, log: [msg, ...s.log].slice(0, 40) };
}

function clearFresh(s: ProtoState): ProtoState {
  return { ...s, episodes: s.episodes.map((e) => (e.fresh ? { ...e, fresh: false } : e)) };
}

export function beginPlanning(s: ProtoState, mode: "toEnd" | "batch", instructions: string): ProtoState {
  return withLog({ ...clearFresh(s), planning: { mode, batchesDone: 0 }, lastInstructions: instructions },
    `开始${mode === "toEnd" ? "规划到源文结尾" : "先规划一批"}（附加指令：${instructions || "无"}）`);
}

/** 模拟服务端提交一个窗口的结果 */
export function planOneWindow(s: ProtoState): ProtoState {
  if (!s.planning) return s;
  const batch = fakePlanWindow(s.cursor, s.cursor + 7, s.planning.batchesDone * 3);
  let id = maxId(s);
  const added: Ep[] = batch.map((c) => ({
    id: ++id, title: c.title, hook: c.hook, origin: "cut", range: c.range,
    hasArtifacts: false, stale: false, fresh: true,
  }));
  const cursor = batch.length ? batch.at(-1)!.range[1] : s.cursor;
  const done = cursor >= SOURCE_LEN || s.planning.mode === "batch" || batch.length === 0;
  const next: ProtoState = {
    ...s,
    episodes: [...s.episodes, ...added],
    cursor,
    planning: done ? null : { ...s.planning, batchesDone: s.planning.batchesDone + 1 },
  };
  return withLog(next, `提交一批：第 ${added[0]?.id ?? "-"}–${added.at(-1)?.id ?? "-"} 集，游标 → 句 ${cursor}${done ? "（结束）" : ""}`);
}

export function stopPlanning(s: ProtoState): ProtoState {
  return withLog({ ...s, planning: null }, "已停止规划，已提交的批次保留");
}

function markTouched(e: Ep): Ep {
  return e.hasArtifacts ? { ...e, stale: true } : e;
}

export function moveBoundary(s: ProtoState, leftId: number, pos: number): ProtoState {
  const right = nextCut(s, leftId);
  const left = s.episodes.find((e) => e.id === leftId);
  if (!left || !right || !left.range || !right.range) return s;
  if (right.range[0] !== left.range[1]) return s;
  if (pos <= left.range[0] || pos >= right.range[1]) return s;
  const episodes = s.episodes.map((e) => {
    if (e.id === left.id) return markTouched({ ...e, range: [e.range![0], pos] as [number, number] });
    if (e.id === right.id) return markTouched({ ...e, range: [pos, e.range![1]] as [number, number] });
    return e;
  });
  return withLog({ ...s, episodes }, `拖动第 ${left.id}/${right.id} 集边界 → 句 ${pos}`);
}

export function manualCut(s: ProtoState, endPos: number, title?: string): ProtoState {
  if (endPos <= s.cursor || endPos > SOURCE_LEN) return s;
  const id = maxId(s) + 1;
  const ep: Ep = {
    id, title: title || `第 ${id} 集`, hook: "", origin: "cut", range: [s.cursor, endPos],
    hasArtifacts: false, stale: false, fresh: true,
  };
  return withLog({ ...clearFresh(s), episodes: [...s.episodes, ep], cursor: endPos },
    `手工切分：第 ${id} 集 = 句 ${s.cursor}–${endPos}`);
}

/** 拆分 / 合并后，从该集起切出的集依次重排集号，跳过其他来源占用的集号 */
function renumberFrom(s: ProtoState, fromId: number, cuts: Ep[]): Ep[] {
  const reserved = new Set(otherEps(s).map((e) => e.id));
  const before = cutEps(s).filter((e) => e.id < fromId);
  let id = fromId;
  const renum = cuts.map((e) => {
    while (reserved.has(id)) id++;
    return { ...e, id: id++ };
  });
  return [...before, ...renum, ...otherEps(s)];
}

export function splitEp(s: ProtoState, id: number, pos: number): ProtoState {
  const ep = s.episodes.find((e) => e.id === id);
  if (!ep?.range || !canRestructure(s, id) || pos <= ep.range[0] || pos >= ep.range[1]) return s;
  const tail = cutEps(s).filter((e) => e.range![0] > ep.range![0]);
  const a: Ep = { ...ep, range: [ep.range[0], pos] };
  const b: Ep = { ...ep, title: `${ep.title}（下）`, hook: "", range: [pos, ep.range[1]], fresh: true };
  return withLog({ ...s, episodes: renumberFrom(s, id, [a, b, ...tail]) }, `拆分第 ${id} 集 @ 句 ${pos}，其后集号顺延`);
}

export function mergeWithNext(s: ProtoState, id: number): ProtoState {
  const ep = s.episodes.find((e) => e.id === id);
  const nx = nextCut(s, id);
  if (!ep?.range || !nx?.range || !canRestructure(s, id)) return s;
  const tail = cutEps(s).filter((e) => e.range![0] > nx.range![0]);
  const merged: Ep = { ...ep, range: [ep.range[0], nx.range[1]] };
  return withLog({ ...s, episodes: renumberFrom(s, id, [merged, ...tail]) }, `合并第 ${id} 集与第 ${nx.id} 集，其后集号前移`);
}

/** 移除某一集之后的所有切分：有产物的转为无原文的集并标 stale，没有产物的直接移除 */
export function removeCutsAfter(s: ProtoState, id: number): ProtoState {
  const ep = s.episodes.find((e) => e.id === id);
  if (!ep?.range) return s;
  const after = new Set(cutEps(s).filter((e) => e.range![0] >= ep.range![1]).map((e) => e.id));
  const episodes = s.episodes.flatMap((e) => {
    if (!after.has(e.id)) return [e];
    if (e.hasArtifacts) return [{ ...e, origin: "none" as const, range: undefined, stale: true }];
    return [];
  });
  return withLog({ ...s, episodes, cursor: ep.range[1] }, `移除第 ${id} 集之后的所有切分（${after.size} 集），游标回到句 ${ep.range[1]}`);
}

export function affectedByRemoveAfter(s: ProtoState, id: number): Ep[] {
  const ep = s.episodes.find((e) => e.id === id);
  if (!ep?.range) return [];
  return cutEps(s).filter((e) => e.range![0] >= ep.range![1]);
}

export function addOwnEpisodes(s: ProtoState, files: { name: string; chars: number }[]): ProtoState {
  let id = maxId(s);
  const added: Ep[] = files.map((f) => ({
    id: ++id, title: f.name.replace(/\.[^.]+$/, ""), hook: "", origin: "own", ownChars: f.chars, ownFile: f.name,
    hasArtifacts: false, stale: false, fresh: true,
  }));
  return withLog({ ...clearFresh(s), episodes: [...s.episodes, ...added] },
    `上传逐集原文：${files.map((f, i) => `${f.name}→第 ${maxId(s) + i + 1} 集`).join("，")}`);
}

export function addBlankEpisode(s: ProtoState): ProtoState {
  const id = maxId(s) + 1;
  return withLog({ ...clearFresh(s), episodes: [...s.episodes, { id, title: `第 ${id} 集`, hook: "", origin: "none", hasArtifacts: false, stale: false, fresh: true }] },
    `新建一集：第 ${id} 集（无原文）`);
}

export function replaceSource(s: ProtoState): ProtoState {
  return withLog({ ...s, sourceReplaced: true }, "整本源文已替换：接续规划与从第 N 集起重新规划被拒，只能从第 1 集起重新规划");
}

export function renameEp(s: ProtoState, id: number, title: string, hook: string): ProtoState {
  return { ...s, episodes: s.episodes.map((e) => (e.id === id ? { ...e, title, hook } : e)) };
}

// ---------------------------------------------------------------- 候选

export function beginReplan(s: ProtoState, fromId: number, instructions: string): ProtoState {
  return withLog({ ...s, candidate: { fromEp: fromId, instructions, eps: [], status: "generating", reached: replanStart(s, fromId) } },
    `从第 ${fromId} 集起重新规划：生成候选（账本不动）`);
}

export function replanStart(s: ProtoState, fromId: number): number {
  const ep = s.episodes.find((e) => e.id === fromId);
  return ep?.range?.[0] ?? 0;
}

export function candidateStep(s: ProtoState): ProtoState {
  const c = s.candidate;
  if (!c || c.status !== "generating") return s;
  const batch = fakePlanWindow(c.reached, c.reached + 101, c.eps.length + 2);
  const reached = batch.length ? batch.at(-1)!.range[1] : SOURCE_LEN;
  const done = reached >= SOURCE_LEN;
  return { ...s, candidate: { ...c, eps: [...c.eps, ...batch], reached, status: done ? "ready" : "generating" } };
}

export function stopCandidate(s: ProtoState): ProtoState {
  if (!s.candidate) return s;
  return withLog({ ...s, candidate: { ...s.candidate, status: "stopped" } }, "候选生成中途停止");
}

export interface CandidateSummary {
  replaced: Ep[];
  newCount: number;
  mapping: { newId: number; oldId?: number; cand: CandEp }[];
  staleIds: number[];
  toNone: number[];
  removed: number[];
  /** 中途停止时能否采纳（#2775 的建议：覆盖到结尾或恰好停在旧集边界上） */
  adoptable: boolean;
}

export function candidateSummary(s: ProtoState): CandidateSummary | null {
  const c = s.candidate;
  if (!c) return null;
  const start = replanStart(s, c.fromEp);
  const replaced = cutEps(s).filter((e) => e.range![0] >= start);
  const oldIds = replaced.map((e) => e.id);
  let extra = Math.max(maxId(s), ...oldIds);
  const mapping = c.eps.map((cand, i) => {
    const oldId = oldIds[i];
    return { newId: oldId ?? ++extra, oldId, cand };
  });
  const leftover = replaced.slice(c.eps.length);
  const staleIds = replaced
    .filter((e, i) => e.hasArtifacts && (i >= c.eps.length || c.eps[i].range.join() !== e.range!.join()))
    .map((e) => e.id);
  const boundaries = new Set(cutEps(s).map((e) => e.range![1]));
  const adoptable = c.status === "ready" || (c.status === "stopped" && boundaries.has(c.reached));
  return {
    replaced,
    newCount: c.eps.length,
    mapping,
    staleIds,
    toNone: leftover.filter((e) => e.hasArtifacts).map((e) => e.id),
    removed: leftover.filter((e) => !e.hasArtifacts).map((e) => e.id),
    adoptable,
  };
}

export function adoptCandidate(s: ProtoState): ProtoState {
  const c = s.candidate;
  const sum = candidateSummary(s);
  if (!c || !sum || !sum.adoptable) return s;
  const replacedIds = new Set(sum.replaced.map((e) => e.id));
  const oldById = new Map(sum.replaced.map((e) => [e.id, e]));
  const kept = s.episodes.filter((e) => !replacedIds.has(e.id));
  const fromCand: Ep[] = sum.mapping.map((m) => {
    const old = m.oldId ? oldById.get(m.oldId) : undefined;
    return {
      id: m.newId, title: m.cand.title, hook: m.cand.hook, origin: "cut", range: m.cand.range,
      hasArtifacts: old?.hasArtifacts ?? false, stale: sum.staleIds.includes(m.newId) || (old?.stale ?? false), fresh: true,
    };
  });
  const toNone: Ep[] = sum.toNone.map((id) => ({ ...oldById.get(id)!, origin: "none", range: undefined, stale: true }));
  return withLog({
    ...clearFresh(s),
    episodes: [...kept, ...fromCand, ...toNone],
    cursor: c.reached,
    candidate: null,
    sourceReplaced: c.fromEp === cutEps(s)[0]?.id ? false : s.sourceReplaced,
  }, `采纳候选：替换第 ${sum.replaced.map((e) => e.id).join("、")} 集，stale：${sum.staleIds.join("、") || "无"}`);
}

export function discardCandidate(s: ProtoState): ProtoState {
  return withLog({ ...s, candidate: null }, "放弃候选，账本未改动");
}
