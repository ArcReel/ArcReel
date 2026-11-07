import type { EpisodesView, ReplanSummary } from "@/types";

import { toGlobal } from "./manual-split-model";

/** One right-side source-bar segmentation: candidate episode `index` or not-yet-generated source in replan scope. */
export type CompareLane = { kind: "new"; index: number } | { kind: "pending" };

/** Right-side bar segment within one source line; `[from, to)` are file-local code-point offsets. */
export interface LanePiece {
  from: number;
  to: number;
  lane: CompareLane;
}

/**
 * Source-position comparison: left bars are existing episode segmentation, right bars new plans.
 * Cover only text after replan start; ungenerated portions await planning.
 */
export interface ReplanCompare {
  /** Right-side bar segments for source line `[start, end)`, position-sorted, excluding portions before start. */
  lanes: (file: number, start: number, end: number) => LanePiece[];
  /**
   * Differing file-local old/new boundaries, ascending. Compare source positions: cross-file episodes do not split at file edges; whitespace boundaries use
   * the following first non-whitespace character, or next file start when in trailing whitespace.
   */
  diffs: (file: number) => number[];
  /** Existing episodes whose entire sources lie beyond generated candidate scope. */
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

/** Whether adjacent bars join: previous end/following start share an episode or both await planning. */
export function lanesContinue(previous: LanePiece[], next: LanePiece[]): boolean {
  return sameLane(previous.at(-1)?.lane, next[0]?.lane);
}

/** First non-whitespace position from `pos`; continue to next file start at file end, unchanged if segment text is unreadable. */
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

/** Null without candidates or when outdated by episode/source edits since generation: positions no longer match, so no comparison. */
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
      // Awaiting planning: after candidate end, not before its start.
      const pendingFrom =
        file < end[0] || file < start[0]
          ? Infinity
          : Math.max(file === end[0] ? end[1] : 0, file === start[0] ? start[1] : 0);
      if (pendingFrom < to) pieces.push({ from: Math.max(pendingFrom, from), to, lane: { kind: "pending" } });
    }
    return pieces.sort((a, b) => a.from - b.from);
  };

  // Compare global code-point boundaries: file A end and file B start are the same position.
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

  // Existing episodes wholly beyond candidate end; cross-file episodes use the portion in their starting file.
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
