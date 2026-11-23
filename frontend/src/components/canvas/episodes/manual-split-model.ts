import type { EpisodesView } from "@/types";

export interface ManuscriptPoint {
  file: number;
  offset: number;
}

export type PointAction =
  | { kind: "cut"; file: number; offset: number; start: number; end: number }
  | { kind: "split"; file: number; offset: number; episode: number; start: number; at: number; end: number }
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

export type MovingBoundary = number | null;

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

export interface TextRun {
  start: number;
  text: string;
}

export function textRuns(text: string, base: number): TextRun[] {
  const runs: TextRun[] = [];
  let offset = base;
  for (const line of text.split("\n")) {
    if (line.trim() !== "") runs.push({ start: offset, text: line });
    offset += [...line].length + 1;
  }
  return runs;
}

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

export function toGlobal(view: EpisodesView, point: ManuscriptPoint): number {
  return bases(view)[point.file] + point.offset;
}

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

export function caretEpisode(action: PointAction): number | null {
  return action.kind === "split" ? action.episode : null;
}

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

const ZH_UNIT = /[\u3400-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef\u{20000}-\u{323af}]/gu;
const WORD_UNIT = /[\p{L}\p{N}_]+/gu;

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
