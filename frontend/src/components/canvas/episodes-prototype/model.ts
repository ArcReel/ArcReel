// PROTOTYPE（#2831，基于 #2767，一次性代码，勿合入 main）：多文件整本源文 +「分集」视图的内存数据模型。
// 整本源文由多个文件按项目记录的顺序拼成；切出集的原文范围记为「文件 + 文件内偏移」，不跨文件（#2830）。
// 账本条目顺序即播出顺序，界面上的「第 N 集」按位置派生，id 只做内部身份与配色（#2795）。
// 重新规划沿用 #2767 原型的逻辑（候选沿用旧 id），不在本票评审范围。

export type Origin = "cut" | "own" | "none";
export type Kind = "novel" | "script";
export const KIND_LABEL: Record<Kind, string> = { novel: "小说", script: "剧本" };

export interface SrcFile {
  id: string;
  name: string;
  kind: Kind;
  text: string;
  /** 在 ArcReel 之外被改动后磁盘上的新内容；text 仍是上次登记时的快照 */
  pendingText?: string;
}

export interface Loc { file: string; a: number; b: number }

export interface Ep {
  id: number;
  title: string;
  hook: string;
  origin: Origin;
  /** 切出集：文件内的 [起, 止) */
  loc?: Loc;
  /** 派生：整本源文拼接后的全局偏移，只供渲染 */
  range?: [number, number];
  ownChars?: number;
  ownFile?: string;
  ownKind?: Kind;
  hasArtifacts: boolean;
  stale: boolean;
  fresh?: boolean;
}

export interface CandEp { title: string; hook: string; range: [number, number] }

export interface Candidate {
  fromEp: number;
  /** 重新规划起点（全局偏移），发起时记下 */
  start: number;
  instructions: string;
  eps: CandEp[];
  status: "generating" | "ready" | "stopped";
  reached: number;
  /** 「先规划一批」：一窗后停下，可以继续 */
  batch: boolean;
}

export interface Planning { from: number; pos: number; until: number; gap: boolean; batch: boolean }

export interface ProtoState {
  files: SrcFile[];
  episodes: Ep[];
  planning: Planning | null;
  candidate: Candidate | null;
  lastInstructions: string;
  log: string[];
  /** 历史最高集 ID：只分配、不复用（#2795） */
  idHigh: number;
}

// ---------------------------------------------------------------- 假源文

const CORPUS = [
  "雨下了整整一夜，码头上的灯一盏接一盏灭了。", "沈砚把领口竖起来，站在仓库门口没有动。", "他知道今晚来的人不会只有一个。",
  "“东西带来了吗？”黑暗里有人开口，嗓音沙哑。", "沈砚没有回答，只是把手里的信封往前递了半寸。", "对方笑了一声，那笑声让人想起生锈的铁门。",
  "许知意坐在车里，隔着起雾的玻璃盯着这一幕。", "她答应过父亲，绝不再踏进这座城半步。", "可父亲已经死了，承诺也就跟着埋进了土里。",
  "三年前那场火烧掉了许家的半条街，也烧掉了所有账本。", "所有人都说那是意外，只有她不信。", "沈砚回到车上时，衣角还在往下滴水。",
  "“他们要的不是钱。”他说，“他们要一个名字。”", "许知意转过头，第一次认真地看着这个男人。", "她想起老管家临终前说的那句话：别相信姓沈的。",
  "可眼下，她能信的人只剩他一个。", "第二天一早，报纸头版登出了仓库失火的消息。", "照片上的废墟里，隐约能看见半截烧焦的招牌。",
  "陆警官把报纸摔在桌上，烟灰落了一桌子。", "“又是许家的旧账。”他盯着窗外，声音压得很低。", "局里没人愿意碰这个案子，上一个碰的人已经调去了边境。",
  "许知意推门进来的时候，陆警官的烟刚好燃尽。", "“我要看三年前的卷宗。”她说得很平静。", "陆警官沉默了很久，最后从抽屉里摸出一把钥匙。",
  "档案室在地下二层，灯管坏了一半，走廊尽头一片漆黑。", "卷宗比她想象的薄得多，关键的几页被人撕掉了。", "撕口很整齐，像是用尺子比着裁下来的。",
  "她在最后一页的背面发现了一行铅笔字，字迹熟悉得让她发抖。", "那是父亲的笔迹，写的是一个日期和一个码头编号。", "沈砚看到那行字时，脸色第一次变了。",
  "“这个编号，”他慢慢说，“是我家的船。”", "窗外又开始下雨，雨点敲在铁皮屋檐上，像有人在数着什么。", "许知意把卷宗合上，指节因为用力而发白。",
  "她终于明白，这座城里没有一个人是干净的。", "包括她自己。", "夜里，有人往她住的旅馆门缝里塞了一张空白信封。",
  "信封里什么都没有，只有一股淡淡的烟草味。", "她认得那个味道，那是父亲生前最爱的牌子。", "沈砚说这是警告，她却觉得这是邀请。",
  "天亮之前，他们决定再去一次码头。",
];
const CHAPTER_NAMES = ["雨夜", "旧账", "卷宗", "空白信封", "船号", "火起", "证人", "黎明", "反咬", "摊牌", "潮信", "余烬"];
const CN_NUM = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二", "十三", "十四", "十五"];
const SPEAKERS = ["沈砚", "许知意", "陆警官", "老管家"];

function rnd(n: number): number {
  const x = Math.sin(n * 12.9898 + 78.233) * 43758.5453;
  return x - Math.floor(x);
}

export function genNovel(seed: number, chapFrom: number, chapCount: number, perChap = 40): string {
  let text = "";
  for (let c = 0; c < chapCount; c++) {
    const n = chapFrom + c;
    text += `${text ? "\n" : ""}第${CN_NUM[n] ?? n + 1}章 ${CHAPTER_NAMES[n % CHAPTER_NAMES.length]}\n`;
    for (let i = 0; i < perChap; i++) {
      if (i > 0 && rnd(seed * 97 + c * 41 + i) < 0.3) text += "\n";
      text += CORPUS[(seed * 11 + c * 7 + i * 3) % CORPUS.length];
    }
  }
  return text;
}

export function genScript(seed: number, sceneFrom: number, sceneCount: number, perScene = 18): string {
  let text = "";
  for (let c = 0; c < sceneCount; c++) {
    const n = sceneFrom + c;
    text += `${text ? "\n" : ""}第${CN_NUM[n] ?? n + 1}场 ${CHAPTER_NAMES[(n + 4) % CHAPTER_NAMES.length]} · 夜\n`;
    for (let i = 0; i < perScene; i++) {
      const line = CORPUS[(seed * 13 + c * 5 + i * 7) % CORPUS.length];
      text += i % 3 === 0 ? `△ ${line}\n` : `${SPEAKERS[(i + c) % SPEAKERS.length]}：${line}\n`;
    }
  }
  return text.replace(/\n$/, "");
}

// ---------------------------------------------------------------- 版面（整本源文拼接后的全局视图）

export interface FileSpan { id: string; name: string; kind: Kind; start: number; end: number; pending: boolean }
export interface Layout {
  text: string;
  len: number;
  files: FileSpan[];
  paraStarts: number[];
  chapters: [number, number][];
  sentenceEnds: number[];
}

const layoutCache = new WeakMap<SrcFile[], Layout>();

export function L(s: { files: SrcFile[] }): Layout {
  const hit = layoutCache.get(s.files);
  if (hit) return hit;
  let text = "";
  const files: FileSpan[] = [];
  for (const f of s.files) {
    const start = text.length;
    text += f.text;
    files.push({ id: f.id, name: f.name, kind: f.kind, start, end: text.length, pending: f.pendingText != null });
  }
  const para = new Set<number>(files.map((f) => f.start));
  const ends = new Set<number>(files.map((f) => f.end));
  const chapters: [number, number][] = [];
  const lineStarts = new Set<number>(files.map((f) => f.start));
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === "\n") { ends.add(i); if (i + 1 < text.length) { para.add(i + 1); lineStarts.add(i + 1); } }
    if ("。！？".includes(c)) ends.add(text[i + 1] === "”" ? i + 2 : i + 1);
  }
  for (const ls of lineStarts) {
    const m = /^第[^\s]{1,4}[章场] [^\n]*/.exec(text.slice(ls, ls + 40));
    if (m) chapters.push([ls, ls + m[0].length]);
  }
  const out: Layout = {
    text, len: text.length, files,
    paraStarts: [...para].sort((a, b) => a - b),
    chapters: chapters.sort((a, b) => a[0] - b[0]),
    sentenceEnds: [...ends].sort((a, b) => a - b),
  };
  layoutCache.set(s.files, out);
  return out;
}

export function fileAt(s: ProtoState, pos: number): FileSpan {
  const fs = L(s).files;
  return fs.find((f) => f.start <= pos && pos < f.end) ?? fs[fs.length - 1];
}
export function fileSpan(s: ProtoState, id: string): FileSpan | undefined {
  return L(s).files.find((f) => f.id === id);
}
export function charsOf(s: ProtoState, range: [number, number]): number {
  const [a, b] = range[0] <= range[1] ? range : [range[1], range[0]];
  return L(s).text.slice(a, b).replace(/\n/g, "").length;
}
export function totalChars(s: ProtoState): number {
  return charsOf(s, [0, L(s).len]);
}
export function epChars(s: ProtoState, ep: Ep): number {
  if (ep.origin === "cut" && ep.range) return charsOf(s, ep.range);
  return ep.ownChars ?? 0;
}
export function readLabel(chars: number): string {
  const min = chars / 250;
  return `${chars.toLocaleString()} 字 · 约 ${min < 1 ? "<1" : min.toFixed(1)} 分钟`;
}
const EXCERPT = 22;
export function headExcerpt(s: ProtoState, range?: [number, number]): string {
  if (!range) return "";
  const t = L(s).text.slice(range[0], range[1]).replace(/\n/g, " ").trim();
  return t.length > EXCERPT ? `${t.slice(0, EXCERPT)}…` : t;
}
export function tailExcerpt(s: ProtoState, range?: [number, number]): string {
  if (!range) return "";
  const t = L(s).text.slice(range[0], range[1]).replace(/\n/g, " ").trim();
  return t.length > EXCERPT ? `…${t.slice(-EXCERPT)}` : t;
}

/** 原型里的「窗口」（字符），对应真实的 5 万字 */
export const WINDOW = 2400;

// ---------------------------------------------------------------- 账本查询

export function cutEps(s: ProtoState): Ep[] {
  return s.episodes.filter((e) => e.origin === "cut").sort((a, b) => a.range![0] - b.range![0]);
}
export function otherEps(s: ProtoState): Ep[] {
  return s.episodes.filter((e) => e.origin !== "cut");
}
export function maxId(s: ProtoState): number {
  return s.episodes.reduce((m, e) => Math.max(m, e.id), s.idHigh);
}
export function posOf(s: ProtoState, id: number): number {
  return s.episodes.findIndex((e) => e.id === id) + 1;
}
export function label(s: ProtoState, id: number): string {
  const n = posOf(s, id);
  return n > 0 ? `第 ${n} 集` : "—";
}
export function cursor(s: ProtoState): number {
  return cutEps(s).reduce((m, e) => Math.max(m, e.range![1]), 0);
}
export function nextCut(s: ProtoState, id: number): Ep | undefined {
  const list = cutEps(s);
  const i = list.findIndex((e) => e.id === id);
  const nx = i >= 0 ? list[i + 1] : undefined;
  return nx && nx.loc!.file === list[i].loc!.file && nx.loc!.a === list[i].loc!.b ? nx : undefined;
}
export function canRestructure(s: ProtoState, id: number): boolean {
  const list = cutEps(s);
  const i = list.findIndex((e) => e.id === id);
  return i >= 0 && list.slice(i).every((e) => !e.hasArtifacts);
}
export function epsInFile(s: ProtoState, fileId: string): Ep[] {
  return cutEps(s).filter((e) => e.loc!.file === fileId);
}
export function fileCoverage(s: ProtoState, fileId: string): { cut: number; total: number } {
  const f = fileSpan(s, fileId);
  if (!f) return { cut: 0, total: 0 };
  return { cut: epsInFile(s, fileId).reduce((n, e) => n + epChars(s, e), 0), total: charsOf(s, [f.start, f.end]) };
}

export interface Span { a: number; b: number; file: FileSpan; gap: boolean }
/** 未切分的原文：按文件切开；规划游标之前的是切出集之间的空段，之后的是尚未规划的部分 */
export function spans(s: ProtoState): Span[] {
  const cur = cursor(s);
  const cuts = cutEps(s);
  const out: Span[] = [];
  for (const f of L(s).files) {
    let x = f.start;
    for (const c of cuts.filter((e) => e.loc!.file === f.id)) {
      if (c.range![0] > x) out.push({ a: x, b: c.range![0], file: f, gap: true });
      x = c.range![1];
    }
    if (x < f.end) out.push({ a: x, b: f.end, file: f, gap: true });
  }
  return out.filter((sp) => charsOf(s, [sp.a, sp.b]) > 0).map((sp) => ({ ...sp, gap: sp.b <= cur }));
}
export function spanAt(s: ProtoState, pos: number): Span | undefined {
  return spans(s).find((sp) => sp.a < pos && pos <= sp.b);
}

// ---------------------------------------------------------------- 归一：派生全局范围 + 按锚点排播出顺序

function sync(s: ProtoState): ProtoState {
  const st = new Map(L(s).files.map((f) => [f.id, f.start]));
  return {
    ...s,
    episodes: s.episodes.map((e) =>
      e.origin === "cut" && e.loc
        ? { ...e, range: [st.get(e.loc.file)! + e.loc.a, st.get(e.loc.file)! + e.loc.b] as [number, number] }
        : { ...e, range: undefined, loc: undefined }),
  };
}

/** 切出集之间锁定源文顺序；其他来源的集跟着它前面最近的切出集（锚点）走 */
function normalize(s0: ProtoState): ProtoState {
  const s = sync(s0);
  const head: Ep[] = [];
  const groups = new Map<number, Ep[]>();
  let anchor: number | null = null;
  for (const e of s.episodes) {
    if (e.origin === "cut") { anchor = e.id; groups.set(e.id, []); }
    else if (anchor == null) head.push(e);
    else groups.get(anchor)!.push(e);
  }
  const cuts = s.episodes.filter((e) => e.origin === "cut").sort((a, b) => a.range![0] - b.range![0]);
  return { ...s, idHigh: maxId(s), episodes: [...head, ...cuts.flatMap((c) => [c, ...groups.get(c.id)!])] };
}

/** 退下的集：有产物的转为无原文并标 stale，移到播出顺序末尾；没有产物的直接移除 */
function retire(s: ProtoState, ids: Set<number>): ProtoState {
  const kept = s.episodes.filter((e) => !ids.has(e.id));
  const back = s.episodes
    .filter((e) => ids.has(e.id) && e.hasArtifacts)
    .map((e) => ({ ...e, origin: "none" as const, loc: undefined, range: undefined, stale: true }));
  const t = normalize({ ...s, episodes: kept });
  return { ...t, idHigh: maxId(s), episodes: [...t.episodes, ...back] };
}

function locOf(s: ProtoState, a: number, b: number): Loc {
  const f = fileAt(s, a);
  return { file: f.id, a: a - f.start, b: b - f.start };
}

function withLog(s: ProtoState, msg: string): ProtoState {
  return { ...s, log: [msg, ...s.log].slice(0, 40) };
}
function clearFresh(s: ProtoState): ProtoState {
  return { ...s, episodes: s.episodes.map((e) => (e.fresh ? { ...e, fresh: false } : e)) };
}

/** 新切出的集插入播出顺序：接续规划紧接最后一个切出集；空段里的集排在前一个切出集（及跟着它的集）之后 */
function insertCuts(s: ProtoState, eps: Ep[], at: number): ProtoState {
  const ledger = [...s.episodes];
  const cuts = cutEps(s);
  const prev = cuts.filter((c) => c.range![1] <= at).at(-1);
  const isTail = !cuts.some((c) => c.range![0] >= at);
  let idx: number;
  if (!prev) {
    const first = ledger.findIndex((e) => e.origin === "cut");
    idx = first >= 0 ? first : ledger.length;
  } else {
    idx = ledger.findIndex((e) => e.id === prev.id) + 1;
    if (!isTail) while (idx < ledger.length && ledger[idx].origin !== "cut") idx++;
  }
  ledger.splice(idx, 0, ...eps);
  return normalize({ ...s, episodes: ledger });
}

// ---------------------------------------------------------------- 假 AI

const TITLES = [
  "雨夜来客", "码头对峙", "她的条件", "空白信封", "逃不出的城", "第二次交易", "火起", "沉默的证人",
  "反咬", "摊牌", "黎明前", "旧船号", "撕掉的三页", "父亲的笔迹", "地下二层", "调去边境的人",
];
const HOOKS = [
  "信封递出去的那一刻，对方叫出了她父亲的名字。", "卷宗最后一页的铅笔字，指向一艘早已沉没的船。",
  "陆警官交出钥匙前，只提了一个条件。", "旅馆门缝里又多了一个信封，这次里面有东西。",
  "沈砚终于承认，三年前他就在码头上。", "火光里走出来的人，本该已经死了。",
];

/** 一个窗口：从 from 起最多 WINDOW 字，不跨文件（切出集不跨文件，窗口遇文件边界即截断） */
function planChunk(s: ProtoState, from: number, until: number, seed: number): { eps: CandEp[]; reached: number } {
  const lay = L(s);
  const f = fileAt(s, from);
  const hardEnd = Math.min(f.end, until);
  const windowEnd = Math.min(from + WINDOW, hardEnd);
  const out: CandEp[] = [];
  let pos = from;
  while (pos < hardEnd) {
    const len = 520 + Math.floor(rnd(seed * 31 + pos) * 300);
    const end = lay.sentenceEnds.find((e) => e >= pos + len) ?? hardEnd;
    if (end >= hardEnd || (windowEnd === hardEnd && hardEnd - end < 260)) {
      if (windowEnd === hardEnd) { out.push(mkCand(pos, hardEnd, seed + out.length)); pos = hardEnd; }
      break;
    }
    if (end > windowEnd) break;
    out.push(mkCand(pos, end, seed + out.length));
    pos = end;
  }
  return { eps: out, reached: pos === from ? windowEnd : pos };
}

function mkCand(a: number, b: number, k: number): CandEp {
  return { title: TITLES[(a + k) % TITLES.length], hook: HOOKS[(a * 3 + k) % HOOKS.length], range: [a, b] };
}

// ---------------------------------------------------------------- 初始账本

export function initialState(): ProtoState {
  const files: SrcFile[] = [
    { id: "f1", name: "雨夜码头·卷一.txt", kind: "novel", text: genNovel(1, 0, 3) },
    { id: "f2", name: "雨夜码头·卷二.txt", kind: "novel", text: genNovel(2, 3, 2) },
    { id: "f3", name: "卷三·旧船（剧本稿）.txt", kind: "script", text: genScript(3, 0, 5) },
  ];
  const s0: ProtoState = { files, episodes: [], planning: null, candidate: null, lastInstructions: "每集结尾停在人物做出决定之前", log: [], idHigh: 0 };
  const lay = L(s0);
  let id = 0;
  const eps: Ep[] = [];
  const cutFile = (fid: string, limit: number, arts: number) => {
    const f = lay.files.find((x) => x.id === fid)!;
    let pos = f.start;
    let k = 0;
    while (pos < f.end && k < limit) {
      const { eps: batch, reached } = planChunk(s0, pos, f.end, id + 3);
      for (const c of batch) {
        if (k >= limit) break;
        eps.push({ id: ++id, title: c.title, hook: c.hook, origin: "cut", loc: locOf(s0, c.range[0], c.range[1]), hasArtifacts: k < arts, stale: false });
        pos = c.range[1];
        k++;
      }
      if (batch.length === 0) pos = reached;
    }
  };
  cutFile("f1", 99, 3);
  cutFile("f3", 2, 1);
  eps.push(
    { id: ++id, title: "番外·雨夜", hook: "", origin: "own", ownChars: 2140, ownFile: "番外_雨夜.txt", ownKind: "novel", hasArtifacts: true, stale: false },
    { id: ++id, title: "预告片", hook: "", origin: "none", hasArtifacts: false, stale: false },
  );
  return withLog(normalize({ ...s0, episodes: eps }),
    "初始：卷一全部切出（前 3 集已开始制作）；卷二是后来插入的，尚未分集（空段）；卷三（剧本）切出 2 集；另有自带原文、无原文的集各一");
}

// ---------------------------------------------------------------- 规划

export function beginPlanning(s: ProtoState, instructions: string, batch = false): ProtoState {
  const cur = cursor(s);
  return withLog({ ...clearFresh(s), planning: { from: cur, pos: cur, until: L(s).len, gap: false, batch }, lastInstructions: instructions },
    `AI ${batch ? "先规划一批" : "规划到源文结尾"}（附加要求：${instructions || "无"}）`);
}

export function beginGapPlanning(s: ProtoState, a: number, b: number): ProtoState {
  return withLog({ ...clearFresh(s), planning: { from: a, pos: a, until: b, gap: true, batch: false } },
    `规划这段未切分的原文：${fileAt(s, a).name} ${charsOf(s, [a, b])} 字，不替换任何集，直接提交`);
}

export function planOneWindow(s: ProtoState): ProtoState {
  const p = s.planning;
  if (!p) return s;
  const { eps, reached } = planChunk(s, p.pos, p.until, p.pos + 7);
  let id = maxId(s);
  const added: Ep[] = eps.map((c) => ({
    id: ++id, title: c.title, hook: c.hook, origin: "cut", loc: locOf(s, c.range[0], c.range[1]),
    hasArtifacts: false, stale: false, fresh: true,
  }));
  let next = added.length ? insertCuts(s, added, eps[0].range[0]) : s;
  const done = reached >= p.until || p.batch;
  next = { ...next, planning: done ? null : { ...p, pos: reached } };
  return withLog(next, `提交一窗：${added.map((e) => label(next, e.id)).join("、") || "无"}${done ? "（结束）" : ""}`);
}

export function stopPlanning(s: ProtoState): ProtoState {
  return withLog({ ...s, planning: null }, "已停止规划，已提交的集保留");
}

// ---------------------------------------------------------------- 手工切分

export function cutAt(s: ProtoState, pos: number, title?: string): ProtoState {
  const sp = spanAt(s, pos);
  if (!sp) return s;
  const id = maxId(s) + 1;
  const ep: Ep = { id, title: title || "新的一集", hook: "", origin: "cut", loc: locOf(s, sp.a, pos), hasArtifacts: false, stale: false, fresh: true };
  const next = insertCuts(clearFresh(s), [ep], sp.a);
  return withLog(next, `手工切分：${label(next, id)} = ${sp.file.name} ${charsOf(s, [sp.a, pos])} 字`);
}

function touch(e: Ep): Ep {
  return e.hasArtifacts ? { ...e, stale: true } : e;
}

export function moveBoundary(s: ProtoState, leftId: number, pos: number): ProtoState {
  const right = nextCut(s, leftId);
  const left = s.episodes.find((e) => e.id === leftId);
  if (!left?.range || !right?.range || pos <= left.range[0] || pos >= right.range[1]) return s;
  const f = fileSpan(s, left.loc!.file)!;
  const episodes = s.episodes.map((e) => {
    if (e.id === left.id) return touch({ ...e, loc: { ...e.loc!, b: pos - f.start } });
    if (e.id === right.id) return touch({ ...e, loc: { ...e.loc!, a: pos - f.start } });
    return e;
  });
  return withLog(normalize({ ...s, episodes }), `移动 ${label(s, left.id)} / ${label(s, right.id)} 的分界`);
}

export function splitEp(s: ProtoState, id: number, pos: number): ProtoState {
  const ep = s.episodes.find((e) => e.id === id);
  if (!ep?.range || !canRestructure(s, id) || pos <= ep.range[0] || pos >= ep.range[1]) return s;
  const f = fileSpan(s, ep.loc!.file)!;
  const nid = maxId(s) + 1;
  const ledger = s.episodes.flatMap((e) => (e.id === id
    ? [{ ...e, loc: { ...e.loc!, b: pos - f.start } }, { ...e, id: nid, title: `${e.title}（下）`, hook: "", loc: { ...e.loc!, a: pos - f.start }, fresh: true }]
    : [e]));
  return withLog(normalize({ ...s, episodes: ledger }), `拆分 ${label(s, id)}`);
}

export function mergeWithNext(s: ProtoState, id: number): ProtoState {
  const ep = s.episodes.find((e) => e.id === id);
  const nx = nextCut(s, id);
  if (!ep || !nx || !canRestructure(s, id)) return s;
  const episodes = s.episodes.filter((e) => e.id !== nx.id).map((e) => (e.id === id ? { ...e, loc: { ...e.loc!, b: nx.loc!.b } } : e));
  return withLog(normalize({ ...s, episodes }), `合并 ${label(s, id)} 与 ${label(s, nx.id)}`);
}

export function affectedByRemoveAfter(s: ProtoState, id: number): Ep[] {
  const ep = s.episodes.find((e) => e.id === id);
  if (!ep?.range) return [];
  return cutEps(s).filter((e) => e.range![0] >= ep.range![1]);
}

export function removeCutsAfter(s: ProtoState, id: number): ProtoState {
  const ids = new Set(affectedByRemoveAfter(s, id).map((e) => e.id));
  return withLog(retire(s, ids), `清除 ${label(s, id)} 之后的所有切分（${ids.size} 集）`);
}

/** 自带原文与无原文的集可以插在任意一集之后，默认放在末尾（#2795） */
function insertOthers(s: ProtoState, eps: Ep[], afterId: number | null): ProtoState {
  const ledger = [...s.episodes];
  const idx = afterId == null ? ledger.length : ledger.findIndex((e) => e.id === afterId) + 1;
  ledger.splice(idx, 0, ...eps);
  return normalize({ ...clearFresh(s), episodes: ledger });
}

export function addOwnEpisodes(s: ProtoState, files: { name: string; chars: number; kind: Kind }[], afterId: number | null = null): ProtoState {
  let id = maxId(s);
  const added: Ep[] = files.map((f) => ({
    id: ++id, title: f.name.replace(/\.[^.]+$/, ""), hook: "", origin: "own", ownChars: f.chars, ownFile: f.name, ownKind: f.kind,
    hasArtifacts: false, stale: false, fresh: true,
  }));
  return withLog(insertOthers(s, added, afterId), `上传逐集原文 ${files.length} 个${afterId == null ? "，放在末尾" : `，放在${label(s, afterId)}之后`}`);
}

export function addBlankEpisode(s: ProtoState, title = "", afterId: number | null = null): ProtoState {
  const id = maxId(s) + 1;
  const ep: Ep = { id, title: title || "新的一集", hook: "", origin: "none", hasArtifacts: false, stale: false, fresh: true };
  return withLog(insertOthers(s, [ep], afterId), `新建一集（无原文）${afterId == null ? "，放在末尾" : `，放在${label(s, afterId)}之后`}`);
}

/** 直接删除（硬删除）：切出集那段原文释放为未切分的原文 */
export function deleteEp(s: ProtoState, id: number): ProtoState {
  const lab = label(s, id);
  return withLog(normalize({ ...s, episodes: s.episodes.filter((e) => e.id !== id) }), `删除${lab}`);
}

// ---------------------------------------------------------------- 文件操作（#2830）

let fileSeq = 100;
export function newFileId(): string {
  return `f${++fileSeq}`;
}

/** 插入文件：不动任何集，新文件是未切分的原文 */
export function insertFiles(s: ProtoState, added: SrcFile[], order: string[]): ProtoState {
  const all = [...s.files, ...added];
  const files = order.map((id) => all.find((f) => f.id === id)).filter((f): f is SrcFile => !!f);
  return withLog(normalize({ ...clearFresh(s), files }), `新增整本源文文件：${added.map((f) => f.name).join("、")}`);
}

/** 调序：切出集整块跟着文件走，其他来源的集按锚点落位，不标 stale */
export function reorderFiles(s: ProtoState, order: string[]): ProtoState {
  const files = order.map((id) => s.files.find((f) => f.id === id)).filter((f): f is SrcFile => !!f);
  return withLog(normalize({ ...s, files }), `文件调序：${files.map((f) => f.name).join(" → ")}`);
}

/** 删除文件：该文件里的切出集按移除切分处理 */
export function deleteFile(s: ProtoState, id: string): ProtoState {
  const f = s.files.find((x) => x.id === id);
  const ids = new Set(epsInFile(s, id).map((e) => e.id));
  const t = retire(s, ids);
  return withLog(normalize({ ...t, files: t.files.filter((x) => x.id !== id) }), `删除文件 ${f?.name}`);
}

export function setKind(s: ProtoState, id: string, kind: Kind): ProtoState {
  return withLog({ ...s, files: s.files.map((f) => (f.id === id ? { ...f, kind } : f)) }, `源文件类型：${s.files.find((f) => f.id === id)?.name} → ${KIND_LABEL[kind]}`);
}

/** 替换 / 编辑 / 快照对齐：改动前后的文本逐句对齐，逐集映射边界 */
export function rewriteFile(s: ProtoState, id: string, newText: string, opts: { kind?: Kind; name?: string; why: string } = { why: "编辑" }): ProtoState {
  const f = s.files.find((x) => x.id === id);
  if (!f) return s;
  const al = aligner(f.text, newText);
  const inFile = epsInFile(s, id);
  const starts = new Set(inFile.map((e) => e.loc!.a));
  const retired = new Set<number>();
  let floor = 0;
  const mapped = new Map<number, Ep>();
  for (const e of inFile) {
    const a2 = Math.max(floor, al.right(e.loc!.a));
    const b2 = starts.has(e.loc!.b) ? al.right(e.loc!.b) : al.left(e.loc!.b);
    if (b2 <= a2 || newText.slice(a2, b2).replace(/\s/g, "") === "") { retired.add(e.id); continue; }
    const changed = f.text.slice(e.loc!.a, e.loc!.b) !== newText.slice(a2, b2);
    mapped.set(e.id, { ...(changed ? touch(e) : e), loc: { file: id, a: a2, b: b2 } });
    floor = b2;
  }
  const t: ProtoState = {
    ...s,
    files: s.files.map((x) => (x.id === id ? { ...x, text: newText, pendingText: undefined, kind: opts.kind ?? x.kind, name: opts.name ?? x.name } : x)),
    episodes: s.episodes.map((e) => mapped.get(e.id) ?? e),
  };
  return withLog(retire(t, retired), `${opts.why}文件 ${f.name}`);
}

export function applyPending(s: ProtoState, id: string): ProtoState {
  const f = s.files.find((x) => x.id === id);
  if (f?.pendingText == null) return s;
  return rewriteFile(s, id, f.pendingText, { why: "按快照对齐外部改动：" });
}

/** 原型调试：模拟源文件在 ArcReel 之外被改动（磁盘内容变了，快照仍是旧的） */
export function simulateExternal(s: ProtoState, id: string): ProtoState {
  const f = s.files.find((x) => x.id === id);
  if (!f) return s;
  return withLog({ ...s, files: s.files.map((x) => (x.id === id ? { ...x, pendingText: revise(x.text, 5) } : x)) }, `模拟：${f.name} 在 ArcReel 之外被改动`);
}

/** 示例修订版：改写一句、插入一句、删掉一段、末尾追加一段 */
export function revise(text: string, seed = 1): string {
  const toks = tokenize(text);
  const n = toks.length;
  const out: string[] = [];
  const del0 = Math.floor(n * (0.58 + rnd(seed) * 0.04));
  const del1 = del0 + Math.max(4, Math.floor(n * 0.12));
  const ins = Math.floor(n * 0.4);
  const mod = Math.floor(n * 0.22);
  toks.forEach((t, i) => {
    if (i === ins) out.push("她忽然想起，那天夜里码头上其实还有第三个人。");
    if (i >= del0 && i < del1) return;
    out.push(i === mod ? t.replace(/。/, "，谁也没有再说话。") : t);
  });
  return `${out.join("")}\n尾声\n雨停了。码头上只剩一盏灯还亮着，像是在等什么人回来。`;
}

// ---------------------------------------------------------------- 文本对齐（按句 LCS）

function tokenize(t: string): string[] {
  const out: string[] = [];
  let cur = "";
  for (let i = 0; i < t.length; i++) {
    const c = t[i];
    cur += c;
    if (c === "\n" || ("。！？".includes(c) && t[i + 1] !== "”") || (c === "”" && "。！？".includes(t[i - 1]))) { out.push(cur); cur = ""; }
  }
  if (cur) out.push(cur);
  return out;
}

function aligner(oldT: string, newT: string) {
  const A = tokenize(oldT);
  const B = tokenize(newT);
  const n = A.length;
  const m = B.length;
  const W = m + 1;
  const dp = new Int32Array((n + 1) * W);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i * W + j] = A[i] === B[j] ? dp[(i + 1) * W + j + 1] + 1 : Math.max(dp[(i + 1) * W + j], dp[i * W + j + 1]);
    }
  }
  const match = new Int32Array(n).fill(-1);
  for (let i = 0, j = 0; i < n && j < m;) {
    if (A[i] === B[j]) { match[i] = j; i++; j++; }
    else if (dp[(i + 1) * W + j] >= dp[i * W + j + 1]) i++;
    else j++;
  }
  const oa: number[] = [];
  const nb: number[] = [];
  A.reduce((x, t) => { oa.push(x); return x + t.length; }, 0);
  B.reduce((x, t) => { nb.push(x); return x + t.length; }, 0);
  const tokAt = (x: number) => { let i = 0; while (i + 1 < n && oa[i + 1] <= x) i++; return i; };
  return {
    /** 集的起点：新文字插在分界上时归前一集 */
    right(x: number): number {
      if (x >= oldT.length) return newT.length;
      const i = tokAt(x);
      if (match[i] >= 0) return nb[match[i]] + (x - oa[i]);
      for (let k = i + 1; k < n; k++) if (match[k] >= 0) return nb[match[k]];
      return newT.length;
    },
    /** 后面没有紧邻切出集的终点：之后插入的文字留作未切分的原文 */
    left(x: number): number {
      if (x <= 0) return 0;
      const i = tokAt(x - 1);
      if (match[i] >= 0) return nb[match[i]] + (x - oa[i]);
      for (let k = i - 1; k >= 0; k--) if (match[k] >= 0) return nb[match[k]] + A[k].length;
      return 0;
    },
  };
}

// ---------------------------------------------------------------- 影响清单（确认前展示）

export interface Impact {
  moved: { ep: Ep; from: number; to: number }[];
  shifted: Ep[];
  changedStale: Ep[];
  changedPlain: Ep[];
  retired: Ep[];
  removed: Ep[];
  kindStale: Ep[];
}

export function impact(prev: ProtoState, next: ProtoState): Impact {
  const out: Impact = { moved: [], shifted: [], changedStale: [], changedPlain: [], retired: [], removed: [], kindStale: [] };
  const kindOf = (s: ProtoState, e: Ep) => s.files.find((f) => f.id === e.loc?.file)?.kind;
  for (const e of prev.episodes) {
    const n = next.episodes.find((x) => x.id === e.id);
    if (e.origin === "cut") {
      if (!n) { out.removed.push(e); continue; }
      if (n.origin !== "cut") { out.retired.push(e); continue; }
      const t0 = L(prev).text.slice(e.range![0], e.range![1]);
      const t1 = L(next).text.slice(n.range![0], n.range![1]);
      if (t0 !== t1) (n.hasArtifacts ? out.changedStale : out.changedPlain).push(e);
      else if (e.loc!.a !== n.loc!.a || e.loc!.b !== n.loc!.b) out.shifted.push(e);
      if (e.hasArtifacts && kindOf(prev, e) !== kindOf(next, n)) out.kindStale.push(e);
    }
    if (n) {
      const a = posOf(prev, e.id);
      const b = posOf(next, e.id);
      if (a !== b && n.origin === e.origin) out.moved.push({ ep: e, from: a, to: b });
    }
  }
  return out;
}

export function impactEmpty(i: Impact): boolean {
  return !i.shifted.length && !i.changedStale.length && !i.changedPlain.length && !i.retired.length && !i.removed.length && !i.kindStale.length;
}

// ---------------------------------------------------------------- 重新规划（#2775 / #2795 / #2796）
// 候选集一律分配新 ID；起点及以后的旧切出集全部退下（有产物的转为无原文、标 stale、移到末尾，可勾选一并删除）；
// 夹在范围里的其他来源集按锚点（播出顺序中前面最近切出集的原文结尾）跟着剧情落位。

export function replanStart(s: ProtoState, fromId: number): number {
  return s.episodes.find((e) => e.id === fromId)?.range?.[0] ?? 0;
}

export function beginReplan(s: ProtoState, fromId: number, instructions: string, batch = false): ProtoState {
  const start = replanStart(s, fromId);
  return withLog({ ...s, candidate: { fromEp: fromId, start, instructions, eps: [], status: "generating", reached: start, batch } },
    `从${label(s, fromId)}起重新规划（${batch ? "先规划一批" : "规划到源文结尾"}）：生成候选，账本不动`);
}

export function candidateStep(s: ProtoState): ProtoState {
  const c = s.candidate;
  if (!c || c.status !== "generating") return s;
  const { eps, reached } = planChunk(s, c.reached, L(s).len, c.reached + 101);
  const status = reached >= L(s).len ? "ready" : c.batch ? "stopped" : "generating";
  return { ...s, candidate: { ...c, eps: [...c.eps, ...eps], reached, status } };
}

export function stopCandidate(s: ProtoState): ProtoState {
  if (!s.candidate) return s;
  return withLog({ ...s, candidate: { ...s.candidate, status: "stopped" } }, "候选生成中途停止，已完成的部分保留");
}

export function continueCandidate(s: ProtoState): ProtoState {
  if (!s.candidate) return s;
  return withLog({ ...s, candidate: { ...s.candidate, status: "generating" } }, "继续：从候选的结尾往后接着规划，沿用原来的附加要求");
}

/** 候选集将分配的新 ID（与候选集一一对应） */
export function candidateIds(s: ProtoState): number[] {
  const base = maxId(s);
  return s.candidate?.eps.map((_, i) => base + 1 + i) ?? [];
}

function adoptPreview(s: ProtoState, deleteRetired: boolean): ProtoState {
  const c = s.candidate!;
  const ids = candidateIds(s);
  const replaced = cutEps(s).filter((e) => e.range![0] >= c.start);
  const replacedIds = new Set(replaced.map((e) => e.id));
  const newEps: Ep[] = c.eps.map((ce, i) => ({
    id: ids[i], title: ce.title, hook: ce.hook, origin: "cut", loc: locOf(s, ce.range[0], ce.range[1]), range: ce.range,
    hasArtifacts: false, stale: false, fresh: true,
  }));
  const firstIdx = s.episodes.findIndex((e) => replacedIds.has(e.id));
  const pre = firstIdx < 0 ? s.episodes : s.episodes.slice(0, firstIdx);
  const rest = firstIdx < 0 ? [] : s.episodes.slice(firstIdx);
  const before: Ep[] = [];
  const buckets = newEps.map(() => [] as Ep[]);
  let anchor = c.start;
  for (const e of rest) {
    if (replacedIds.has(e.id)) { anchor = e.range![1]; continue; }
    if (anchor <= c.start || newEps.length === 0) { before.push(e); continue; }
    let k = newEps.findIndex((n) => n.range![0] < anchor && anchor <= n.range![1]);
    if (k < 0) k = newEps.reduce((m, n, i) => (n.range![1] <= anchor ? i : m), newEps.length - 1);
    buckets[k].push(e);
  }
  const retired = deleteRetired ? [] : replaced
    .filter((e) => e.hasArtifacts)
    .map((e) => ({ ...e, origin: "none" as const, loc: undefined, range: undefined, stale: true }));
  const episodes = [...pre, ...before, ...newEps.flatMap((n, k) => [n, ...buckets[k]]), ...retired];
  const t = normalize({ ...clearFresh(s), episodes, candidate: null });
  return { ...t, idHigh: Math.max(t.idHigh, ...ids, 0) };
}

export interface CandidateSummary {
  replaced: Ep[];
  newCount: number;
  retiredKept: Ep[];
  removed: Ep[];
  moved: { ep: Ep; from: number; to: number }[];
  adoptable: boolean;
}

export function candidateSummary(s: ProtoState): CandidateSummary | null {
  const c = s.candidate;
  if (!c) return null;
  const replaced = cutEps(s).filter((e) => e.range![0] >= c.start);
  const next = adoptPreview(s, false);
  const moved = s.episodes
    .filter((e) => e.origin !== "cut")
    .map((e) => ({ ep: e, from: posOf(s, e.id), to: posOf(next, e.id) }))
    .filter((m) => m.from !== m.to);
  return {
    replaced, newCount: c.eps.length, moved,
    retiredKept: replaced.filter((e) => e.hasArtifacts),
    removed: replaced.filter((e) => !e.hasArtifacts),
    adoptable: c.status !== "generating" && c.eps.length > 0,
  };
}

export function adoptCandidate(s: ProtoState, deleteRetired = false): ProtoState {
  const sum = candidateSummary(s);
  if (!sum?.adoptable) return s;
  return withLog(adoptPreview(s, deleteRetired),
    `采纳新方案：${sum.replaced.length} 集 → ${sum.newCount} 集${deleteRetired ? "，退下的集一并删除" : ""}`);
}

export function discardCandidate(s: ProtoState): ProtoState {
  return withLog({ ...s, candidate: null }, "放弃新方案，账本未改动");
}

// ---------------------------------------------------------------- 真实项目

export interface RealEpisode {
  episode: number;
  title: string;
  hook?: string;
  source_range?: { source_file?: string; start?: number; end?: number };
  ledger_status?: string;
  item_count?: number;
}

/**
 * 用真实项目的整本源文与分集账本起步。项目只有一个整本源文文件时，为演示多文件，
 * 在最接近三等分的集分界处把它拆成几个文件（只在内存里，不写回项目）。
 */
export function fromReal(opts: { fileName: string; text: string; kind: Kind; episodes: RealEpisode[]; parts: number }): ProtoState {
  const { text, kind, episodes } = opts;
  // 服务端偏移按 Unicode 码位计，转成 JS 字符串下标
  const cp: number[] = [0];
  for (const ch of text) cp.push(cp[cp.length - 1] + ch.length);
  const u = (x: number) => cp[Math.min(Math.max(0, x), cp.length - 1)];
  const cuts = episodes
    .filter((e) => e.source_range?.source_file?.endsWith(opts.fileName) && e.source_range.start != null && e.source_range.end != null)
    .map((e) => ({ e, a: u(e.source_range!.start!), b: u(e.source_range!.end!) }));
  const ends = cuts.map((c) => c.b).filter((b) => b < text.length);
  const splits: number[] = [];
  for (let k = 1; k < opts.parts; k++) {
    const target = (text.length * k) / opts.parts;
    const pool = ends.length ? ends : [...text.matchAll(/\n/g)].map((m) => m.index + 1);
    const best = pool.reduce((m, x) => (Math.abs(x - target) < Math.abs(m - target) ? x : m), pool[0] ?? target);
    if (best > (splits.at(-1) ?? 0) && best < text.length) splits.push(best);
  }
  const bounds = [0, ...splits, text.length];
  const base = opts.fileName.replace(/\.[^.]+$/, "");
  const names = ["上", "中", "下", "四", "五"];
  const files: SrcFile[] = bounds.slice(0, -1).map((a, i) => ({
    id: `r${i + 1}`,
    name: bounds.length > 2 ? `${base}·${names[i] ?? i + 1}.txt` : opts.fileName,
    kind,
    text: text.slice(a, bounds[i + 1]),
  }));
  const fileOf = (x: number) => bounds.findIndex((b, i) => i < bounds.length - 1 && b <= x && x < bounds[i + 1]);
  const eps: Ep[] = episodes.map((e) => {
    const c = cuts.find((x) => x.e === e);
    const common = { id: e.episode, title: e.title, hook: e.hook ?? "", hasArtifacts: e.ledger_status === "consumed" || (e.item_count ?? 0) > 0, stale: e.ledger_status === "stale" };
    if (!c) return { ...common, origin: "none" as const };
    const fi = fileOf(c.a);
    return { ...common, origin: "cut" as const, loc: { file: files[fi].id, a: c.a - bounds[fi], b: c.b - bounds[fi] } };
  });
  const s0: ProtoState = { files, episodes: eps, planning: null, candidate: null, lastInstructions: "", log: [], idHigh: 0 };
  return withLog(normalize(s0),
    `真实项目：${opts.fileName}，${cuts.length} 集切自整本源文${bounds.length > 2 ? `；为演示多文件，在集分界处拆成 ${files.length} 个文件（只在内存里）` : ""}`);
}
