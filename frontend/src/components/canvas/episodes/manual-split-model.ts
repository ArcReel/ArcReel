import type { EpisodesView, EpisodesViewFile } from "@/types";

/**
 * 手工切分的落点：整本源文清单里第 `file` 个文件、文件内第 `offset` 个码位之前。
 *
 * 偏移与后端 `source_range` 同一坐标系（规范化文本的 Unicode 码位），不是 JS 字符串的 UTF-16 下标。
 */
export interface ManuscriptPoint {
  file: number;
  offset: number;
}

/** 落点上能做的手工切分。偏移都是 `file` 内的码位偏移。 */
export type PointAction =
  /** 在未切分的原文上切分：`[start, end)` 成为新的一集。 */
  | { kind: "cut"; file: number; start: number; end: number }
  /** 在切出集内拆分：`[start, at)` 保留集 ID，`[at, end)` 是新的一集。 */
  | { kind: "split"; file: number; episode: number; start: number; at: number; end: number }
  /** 把 `left` 与 `right` 的分界从 `boundary` 移到 `at`。 */
  | {
      kind: "move";
      file: number;
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

/** 落点在整本源文里的全局偏移：前面各文件的码位数之和加文件内偏移。 */
function toGlobal(view: EpisodesView, point: ManuscriptPoint): number {
  return lengths(view)
    .slice(0, point.file)
    .reduce((sum, length) => sum + length, point.offset);
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

function episodeSpans(file: EpisodesViewFile): Span[] {
  return file.segments
    .filter((segment) => segment.kind === "episode" && segment.episode !== null)
    .map((segment) => ({ episode: segment.episode ?? 0, start: segment.start, end: segment.end }))
    .sort((a, b) => a.start - b.start);
}

function movingPair(view: EpisodesView, moving: number): { file: number; left: Span; right: Span } | null {
  for (const [index, file] of view.files.entries()) {
    const spans = episodeSpans(file);
    const at = spans.findIndex((span) => span.episode === moving);
    if (at < 0) continue;
    const right = spans[at + 1];
    return right && right.start === spans[at].end ? { file: index, left: spans[at], right } : null;
  }
  return null;
}

/** 两集之间相连的分界：左侧一集的集 ID 与分界所在的偏移，供左栏放分界按钮。 */
export function adjacentBoundaries(file: EpisodesViewFile): { left: number; right: number; at: number }[] {
  const spans = episodeSpans(file);
  return spans.slice(1).flatMap((right, index) => {
    const left = spans[index];
    return left.end === right.start ? [{ left: left.episode, right: right.episode, at: right.start }] : [];
  });
}

/**
 * 落点上能做的手工切分；做不了时返回 null。
 *
 * - 正在移动分界时，只接受两集合起来的范围之内、原分界之外的落点。
 * - 落在切出集内部为拆分；落在未切分的原文上为切分，新集从这段未切分原文的开头起。
 */
export function resolvePointAction(
  view: EpisodesView,
  point: ManuscriptPoint,
  moving: MovingBoundary = null,
): PointAction | null {
  const file = view.files[point.file];
  if (!file || file.missing || point.offset < 0 || point.offset > file.length) return null;
  const at = point.offset;
  if (moving !== null) {
    const pair = movingPair(view, moving);
    if (!pair || pair.file !== point.file) return null;
    const { left, right } = pair;
    if (!(left.start < at && at < right.end) || at === left.end) return null;
    return {
      kind: "move",
      file: point.file,
      left: left.episode,
      right: right.episode,
      start: left.start,
      boundary: left.end,
      at,
      end: right.end,
    };
  }
  const spans = episodeSpans(file);
  const inside = spans.find((span) => span.start < at && at < span.end);
  if (inside) {
    return { kind: "split", file: point.file, episode: inside.episode, start: inside.start, at, end: inside.end };
  }
  const start = Math.max(0, ...spans.filter((span) => span.end <= at).map((span) => span.end));
  return at > start ? { kind: "cut", file: point.file, start, end: at } : null;
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

/** 文件内 `[start, end)` 的阅读单位数，供操作条预览两侧体量。 */
export function rangeUnits(file: EpisodesViewFile, start: number, end: number, unit: EpisodesView["unit"]): number {
  const pattern = unit === "words" ? WORD_UNIT : ZH_UNIT;
  let count = 0;
  for (const segment of file.segments) {
    const from = Math.max(start, segment.start);
    const to = Math.min(end, segment.end);
    if (from >= to) continue;
    const chars = [...segment.text].slice(from - segment.start, to - segment.start).join("");
    count += chars.match(pattern)?.length ?? 0;
  }
  return count;
}

/** 右栏单集操作的可用性：按源文位置的下一个切出集能否合并、之后有没有切出集。 */
export function cutEpisodeActions(
  view: EpisodesView,
  episode: number,
): { placed: boolean; merge: "ok" | "none" | "across_files"; clearAfter: boolean } {
  const ordered = view.files.flatMap((file, index) => episodeSpans(file).map((span) => ({ ...span, file: index })));
  const at = ordered.findIndex((span) => span.episode === episode);
  if (at < 0) return { placed: false, merge: "none", clearAfter: false };
  const next = ordered[at + 1];
  return {
    placed: true,
    merge: next === undefined ? "none" : next.file === ordered[at].file ? "ok" : "across_files",
    clearAfter: next !== undefined,
  };
}
