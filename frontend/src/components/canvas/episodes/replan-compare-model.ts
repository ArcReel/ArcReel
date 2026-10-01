import type { EpisodesView, ReplanSummary } from "@/types";

import { toGlobal } from "./manual-split-model";

/** 原文右侧色条的一种分法：新方案的第 `index` 集，或重新规划范围里还没生成到的原文。 */
export type CompareLane = { kind: "new"; index: number } | { kind: "pending" };

/** 一行原文里右侧色条的一段：`[from, to)` 是文件内的码位偏移。 */
export interface LanePiece {
  from: number;
  to: number;
  lane: CompareLane;
}

/**
 * 新旧两种分法按源文位置的对照：左侧色条是现有分集（原文分段本身），右侧色条是新方案。
 * 只覆盖重新规划的起点之后；方案还没生成到的部分是「等待规划」。
 */
export interface ReplanCompare {
  /** 一行原文 `[start, end)` 的右侧色条分段，按位置升序；起点之前的部分不出现。 */
  lanes: (file: number, start: number, end: number) => LanePiece[];
  /**
   * 文件内新旧分界不同的位置，升序。分界按源文位置比较：跨文件的一集在文件交界处不算分界；分界落在空白里时取其后
   * 第一个非空白字符，落在文件末尾的空白里时取下一个文件的开头。
   */
  diffs: (file: number) => number[];
  /** 原文全部在方案还没生成到的范围里的现有集。 */
  waiting: ReadonlySet<number>;
}

type Pos = readonly [file: number, offset: number];

function before(a: Pos, b: Pos): boolean {
  return a[0] < b[0] || (a[0] === b[0] && a[1] < b[1]);
}

function sameLane(a: CompareLane | undefined, b: CompareLane | undefined): boolean {
  if (a === undefined || b === undefined || a.kind !== b.kind) return false;
  return a.kind === "pending" || a.index === (b as { index: number }).index;
}

/** 两段相邻色条是否连成一条：前一段的结尾与后一段的开头属于同一集或都在等待规划。 */
export function lanesContinue(previous: LanePiece[], next: LanePiece[]): boolean {
  return sameLane(previous.at(-1)?.lane, next[0]?.lane);
}

/** `pos` 处起跳过空白后的第一个位置；到文件结尾时接着下一个文件的开头。段文字读不到时原样返回。 */
function skipWhitespace(view: EpisodesView, pos: Pos): Pos {
  let [file, at] = pos;
  for (;;) {
    const entry = view.files[file];
    if (entry === undefined) return [file, at];
    const length = entry.missing ? 0 : entry.length;
    if (at >= length && file + 1 < view.files.length) {
      file += 1;
      at = 0;
      continue;
    }
    const segment = entry.segments.find((s) => s.start <= at && at < s.end);
    const char = segment ? [...segment.text][at - segment.start] : undefined;
    if (char === undefined || char.trim() !== "") return [file, at];
    at += 1;
  }
}

/** 候选过时（生成之后分集或源文有改动）或没有候选时为 null：位置已对不上，不做对照。 */
export function replanCompare(view: EpisodesView, replan: ReplanSummary | null): ReplanCompare | null {
  if (replan === null || replan.stale !== null) return null;
  const fileIndex = new Map(view.files.map((file, index) => [file.source_file, index]));
  const at = (sourceFile: string, offset: number): Pos | null => {
    const index = fileIndex.get(sourceFile);
    return index === undefined ? null : [index, offset];
  };
  const start = at(replan.start.source_file, replan.start.offset);
  if (start === null) return null;
  const end = at(replan.end.source_file, replan.end.offset) ?? [view.files.length, 0];
  const drafts = replan.episodes.flatMap((draft, index) => {
    const pos = at(draft.source_file, draft.start);
    return pos === null ? [] : [{ file: pos[0], start: draft.start, end: draft.end, index }];
  });

  const lanes = (file: number, from: number, to: number): LanePiece[] => {
    const pieces: LanePiece[] = [];
    for (const draft of drafts) {
      if (draft.file !== file || draft.end <= from || draft.start >= to) continue;
      pieces.push({ from: Math.max(draft.start, from), to: Math.min(draft.end, to), lane: { kind: "new", index: draft.index } });
    }
    if (!replan.complete) {
      // 等待规划：方案的结尾之后，且不早于起点
      const pendingFrom =
        file < end[0] || file < start[0]
          ? Infinity
          : Math.max(file === end[0] ? end[1] : 0, file === start[0] ? start[1] : 0);
      if (pendingFrom < to) pieces.push({ from: Math.max(pendingFrom, from), to, lane: { kind: "pending" } });
    }
    return pieces.sort((a, b) => a.from - b.from);
  };

  // 分界按全局码位比较：文件 A 的结尾与文件 B 的开头是同一处
  const global = (pos: Pos) => toGlobal(view, { file: pos[0], offset: pos[1] });
  const [startAt, endAt] = [global(start), end[0] < view.files.length ? global(end) : Infinity];
  const boundarySet = (positions: Pos[]) => {
    const set = new Map<number, Pos>();
    for (const pos of positions) {
      const snapped = skipWhitespace(view, pos);
      const key = global(snapped);
      if (startAt < key && key < endAt) set.set(key, snapped);
    }
    return set;
  };
  const old = boundarySet(
    view.files.flatMap((file, index) =>
      file.segments
        .filter((s) => s.kind === "episode")
        .flatMap((s): Pos[] => [...(s.continued ? [] : [[index, s.start] as Pos]), ...(s.continues ? [] : [[index, s.end] as Pos])]),
    ),
  );
  const fresh = boundarySet(drafts.flatMap((d): Pos[] => [[d.file, d.start], [d.file, d.end]]));
  const byFile = new Map<number, number[]>();
  for (const [key, pos] of [...old, ...fresh]) {
    if (old.has(key) && fresh.has(key)) continue;
    byFile.set(pos[0], [...(byFile.get(pos[0]) ?? []), pos[1]]);
  }
  const diffs = (file: number): number[] => [...new Set(byFile.get(file) ?? [])].sort((a, b) => a - b);

  // 原文全在方案结尾之后的现有集；跨文件的集按起点所在文件的那一段判断
  const waiting = new Set<number>();
  if (!replan.complete) {
    view.files.forEach((file, index) => {
      for (const segment of file.segments) {
        if (segment.kind !== "episode" || segment.episode === null || segment.continued) continue;
        const pos: Pos = [index, segment.start];
        if (!before(pos, start) && !before(pos, end)) waiting.add(segment.episode);
      }
    });
  }
  return { lanes, diffs, waiting };
}
