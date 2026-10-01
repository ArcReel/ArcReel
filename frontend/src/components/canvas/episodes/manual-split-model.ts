import type { EpisodesView } from "@/types";

/**
 * 手工切分的落点：整本源文清单里第 `file` 个文件、文件内第 `offset` 个码位之前。
 *
 * 偏移与后端 `source_range` 同一坐标系（规范化文本的 Unicode 码位），不是 JS 字符串的 UTF-16 下标。
 */
export interface ManuscriptPoint {
  file: number;
  offset: number;
}

/**
 * 落点上能做的手工切分。`file` / `offset` 是落点本身（随请求发给服务端）；`start`、`at`、`end`、`boundary`
 * 是整本源文里的全局码位偏移（前面各文件的码位数之和加文件内偏移），一集的原文可以跨文件。
 */
export type PointAction =
  /** 在未切分的原文上切分：`[start, end)` 成为新的一集。 */
  | { kind: "cut"; file: number; offset: number; start: number; end: number }
  /** 在切出集内拆分：`[start, at)` 保留集 ID，`[at, end)` 是新的一集。 */
  | { kind: "split"; file: number; offset: number; episode: number; start: number; at: number; end: number }
  /** 把 `left` 与 `right` 的分界从 `boundary` 移到 `at`。 */
  | {
      kind: "move";
      file: number;
      offset: number;
      left: number;
      right: number;
      start: number;
      boundary: number;
      at: number;
      end: number;
    };

/** 正在移动的分界：左侧一集的集 ID。 */
export type MovingBoundary = number | null;

/** 码位数：`text` 里 UTF-16 下标 `index` 之前有几个码位。下标落在代理对中间时按整个字符之后算。 */
function codePointsBefore(text: string, index: number): number {
  let count = 0;
  let i = 0;
  while (i < index && i < text.length) {
    const code = text.codePointAt(i) ?? 0;
    i += code > 0xffff ? 2 : 1;
    count += 1;
  }
  return count;
}

/** 一行原文：在文件内的码位起点与文字。 */
export interface TextRun {
  start: number;
  text: string;
}

/** 把一段原文按换行拆成非空行，每行带上它在文件内的码位起点（`base` 是这段原文的起点）。 */
export function textRuns(text: string, base: number): TextRun[] {
  const runs: TextRun[] = [];
  let offset = base;
  for (const line of text.split("\n")) {
    if (line.trim() !== "") runs.push({ start: offset, text: line });
    offset += [...line].length + 1;
  }
  return runs;
}

/**
 * 点击落在一行原文里：行在文件内的码位起点是 `runStart`，光标在行文字的 UTF-16 下标 `index` 处。
 * 返回落点；下标超出行文字时落在行尾。
 */
export function pointInRun(file: number, runStart: number, runText: string, index: number): ManuscriptPoint {
  return { file, offset: runStart + codePointsBefore(runText, Math.min(index, runText.length)) };
}

function lengths(view: EpisodesView): number[] {
  return view.files.map((file) => (file.missing ? 0 : file.length));
}

function bases(view: EpisodesView): number[] {
  const result = [0];
  for (const length of lengths(view)) result.push(result[result.length - 1] + length);
  return result;
}

/** 落点在整本源文里的全局偏移：前面各文件的码位数之和加文件内偏移。 */
export function toGlobal(view: EpisodesView, point: ManuscriptPoint): number {
  return bases(view)[point.file] + point.offset;
}

/**
 * 全局偏移换回落点。恰好落在两个文件交界处时取前一个文件的末尾：后一个文件的开头之前没有正文，
 * 不是有意义的分集点。读不到的文件长度为 0，不会被落到。
 */
function fromGlobal(view: EpisodesView, global: number): ManuscriptPoint | null {
  let prefix = 0;
  let last: ManuscriptPoint | null = null;
  for (const [file, length] of lengths(view).entries()) {
    if (length === 0) continue;
    if (global <= prefix + length) return { file, offset: Math.max(global - prefix, 0) };
    prefix += length;
    last = { file, offset: length };
  }
  return last;
}

interface Span {
  episode: number;
  start: number;
  end: number;
}

/** 落位的切出集在整本源文里的全局范围，按起点排序；跨文件的集合成一段。 */
function episodeSpans(view: EpisodesView): Span[] {
  const base = bases(view);
  const spans = new Map<number, Span>();
  view.files.forEach((file, index) => {
    for (const segment of file.segments) {
      if (segment.kind !== "episode" || segment.episode === null) continue;
      const start = base[index] + segment.start;
      const end = base[index] + segment.end;
      const known = spans.get(segment.episode);
      spans.set(segment.episode, known ? { ...known, end } : { episode: segment.episode, start, end });
    }
  });
  return [...spans.values()].sort((a, b) => a.start - b.start);
}

/** 源文件类型切换处的全局偏移：一集的原文不跨过它。非剧情演绎项目没有类型，也就没有切换处。 */
function kindWalls(view: EpisodesView): number[] {
  const base = bases(view);
  const readable = view.files.map((file, index) => ({ file, index })).filter(({ file }) => !file.missing);
  return readable.slice(1).flatMap(({ file, index }, i) =>
    file.source_kind !== readable[i].file.source_kind ? [base[index]] : [],
  );
}

function wallsInside(walls: number[], start: number, end: number): number[] {
  return walls.filter((wall) => start < wall && wall < end);
}

/** 改动后的范围 `[start, end)` 是否新跨过源文件类型的切换处（原来的范围 `old` 里已有的不算）。 */
function crossesNewWall(walls: number[], start: number, end: number, ...old: Span[]): boolean {
  const allowed = new Set(old.flatMap((span) => wallsInside(walls, span.start, span.end)));
  return wallsInside(walls, start, end).some((wall) => !allowed.has(wall));
}

function movingPair(view: EpisodesView, moving: number): { left: Span; right: Span } | null {
  const spans = episodeSpans(view);
  const at = spans.findIndex((span) => span.episode === moving);
  if (at < 0) return null;
  const right = spans[at + 1];
  return right && right.start === spans[at].end ? { left: spans[at], right } : null;
}

/**
 * 两集之间相连的分界，供左栏放分界按钮：左右两集的集 ID，以及右侧一集起点所在的文件（按钮放在那里）。
 * 分界可以落在文件交界上。
 */
export function adjacentBoundaries(view: EpisodesView): { left: number; right: number; file: number }[] {
  const spans = episodeSpans(view);
  const startFile = new Map<number, number>();
  view.files.forEach((file, index) => {
    for (const segment of file.segments) {
      if (segment.kind === "episode" && segment.episode !== null && !segment.continued) {
        startFile.set(segment.episode, index);
      }
    }
  });
  return spans.slice(1).flatMap((right, index) => {
    const left = spans[index];
    const file = startFile.get(right.episode);
    return left.end === right.start && file !== undefined ? [{ left: left.episode, right: right.episode, file }] : [];
  });
}

/**
 * 落点上能做的手工切分；做不了时返回 null。
 *
 * - 正在移动分界时，只接受两集合起来的范围之内、原分界之外的落点，两侧都不能新跨过源文件类型的切换处。
 * - 落在切出集内部为拆分；落在未切分的原文上为切分，新集从这段未切分原文的开头起（可以从前一个文件接过来），
 *   不跨过源文件类型的切换处。
 */
export function resolvePointAction(
  view: EpisodesView,
  point: ManuscriptPoint,
  moving: MovingBoundary = null,
): PointAction | null {
  const file = view.files[point.file];
  if (!file || file.missing || point.offset < 0 || point.offset > file.length) return null;
  const at = toGlobal(view, point);
  const { file: fileIndex, offset } = point;
  const walls = kindWalls(view);
  if (moving !== null) {
    const pair = movingPair(view, moving);
    if (!pair) return null;
    const { left, right } = pair;
    if (!(left.start < at && at < right.end) || at === left.end) return null;
    if (crossesNewWall(walls, left.start, at, left) || crossesNewWall(walls, at, right.end, right)) return null;
    return {
      kind: "move",
      file: fileIndex,
      offset,
      left: left.episode,
      right: right.episode,
      start: left.start,
      boundary: left.end,
      at,
      end: right.end,
    };
  }
  const spans = episodeSpans(view);
  const inside = spans.find((span) => span.start < at && at < span.end);
  if (inside) {
    return { kind: "split", file: fileIndex, offset, episode: inside.episode, start: inside.start, at, end: inside.end };
  }
  if (offset === 0) return null;
  const start = Math.max(
    0,
    ...spans.filter((span) => span.end <= at).map((span) => span.end),
    ...walls.filter((wall) => wall < at),
  );
  return at > start ? { kind: "cut", file: fileIndex, offset, start, end: at } : null;
}

function sameAction(a: PointAction, b: PointAction): boolean {
  if (a.kind !== b.kind) return false;
  return a.kind !== "split" || (b.kind === "split" && a.episode === b.episode);
}

/**
 * ←/→ 微调：沿整本源文移动 `delta` 个码位，可以跨过文件边界。
 *
 * 只在同一种操作里移动（拆分不换集）：目标位置恰是不能落点的单个位置（原分界、刚好切空）时再跨一格，
 * 仍然不行就退回到这个方向上最远的可用位置；一格也动不了时保持原位。
 */
export function stepPoint(
  view: EpisodesView,
  point: ManuscriptPoint,
  delta: number,
  moving: MovingBoundary = null,
): ManuscriptPoint {
  const current = resolvePointAction(view, point, moving);
  if (current === null || delta === 0) return point;
  const total = lengths(view).reduce((sum, length) => sum + length, 0);
  const origin = toGlobal(view, point);
  const direction = Math.sign(delta);
  const target = Math.min(total, Math.max(0, origin + delta));
  const accepts = (global: number): ManuscriptPoint | null => {
    const next = fromGlobal(view, global);
    if (next === null) return null;
    const action = resolvePointAction(view, next, moving);
    return action !== null && sameAction(current, action) ? next : null;
  };
  const beyond = target + direction;
  const ahead = accepts(target) ?? (beyond >= 0 && beyond <= total ? accepts(beyond) : null);
  if (ahead !== null) return ahead;
  for (let global = target - direction; global !== origin; global -= direction) {
    const next = accepts(global);
    if (next !== null) return next;
  }
  return point;
}

// 与后端 `lib/infra/text_metrics.py` 的阅读单位同一口径：中文按汉字与全角标点计，英文、越南文按词计。
const ZH_UNIT = /[\u3400-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef\u{20000}-\u{323af}]/gu;
const WORD_UNIT = /[\p{L}\p{N}_]+/gu;

/** 整本源文里全局范围 `[start, end)` 的阅读单位数，供操作条预览两侧体量。 */
export function rangeUnits(view: EpisodesView, start: number, end: number): number {
  const pattern = view.unit === "words" ? WORD_UNIT : ZH_UNIT;
  const base = bases(view);
  let count = 0;
  view.files.forEach((file, index) => {
    for (const segment of file.segments) {
      const from = Math.max(start, base[index] + segment.start);
      const to = Math.min(end, base[index] + segment.end);
      if (from >= to) continue;
      const offset = base[index] + segment.start;
      const chars = [...segment.text].slice(from - offset, to - offset).join("");
      count += chars.match(pattern)?.length ?? 0;
    }
  });
  return count;
}

/** 右栏单集操作的可用性：按源文位置的下一个切出集能否合并、之后有没有切出集。 */
export function cutEpisodeActions(
  view: EpisodesView,
  episode: number,
): { placed: boolean; merge: "ok" | "none" | "across_kinds"; clearAfter: boolean } {
  const ordered = episodeSpans(view);
  const at = ordered.findIndex((span) => span.episode === episode);
  if (at < 0) return { placed: false, merge: "none", clearAfter: false };
  const own = ordered[at];
  const next = ordered[at + 1];
  let merge: "ok" | "none" | "across_kinds" = "none";
  if (next !== undefined) {
    merge = crossesNewWall(kindWalls(view), own.start, next.end, own, next) ? "across_kinds" : "ok";
  }
  return { placed: true, merge, clearAfter: next !== undefined };
}
