/**
 * Pure supported_durations input/display/detection helpers.
 *
 * All persisted shapes are list[int]. Convert UI ↔ lists,
 * detect consecutive integer sets for slider/button-group rendering, and format display labels.
 */

const MAX_RANGE_SPAN = 30;
const MAX_SINGLE_VALUE = 60;

export type DurationParseErrorCode =
  | "empty_after_split"
  | "non_positive"
  | "exceeds_max"
  | "range_too_large"
  | "range_inverted"
  | "unparseable";

export class DurationParseError extends Error {
  constructor(
    public readonly code: DurationParseErrorCode,
    public readonly params: Record<string, string | number> = {},
  ) {
    super(`${code}:${JSON.stringify(params)}`);
    this.name = "DurationParseError";
  }
}

/**
 * Parse user-entered comma-separated durations, supporting range shorthand.
 *
 * Rules:
 *   - Split on commas; trim segments and omit empty ones
 *   - Single values: ^\d+$ (positive integers, ≤ MAX_SINGLE_VALUE)
 *   - Ranges: ^(\d+)-(\d+)$; min ≤ max and span ≤ MAX_RANGE_SPAN
 *   - Deduplicated ascending output
 * @returns Parsed list; null for whitespace-only input
 * @throws DurationParseError For invalid segments or separator-only input
 */
export function parseDurationInput(text: string): number[] | null {
  const trimmed = text.trim();
  if (!trimmed) return null;

  const segments = trimmed.split(",").map((s) => s.trim()).filter(Boolean);
  if (segments.length === 0) {
    throw new DurationParseError("empty_after_split", { input: trimmed });
  }
  const result = new Set<number>();

  for (const seg of segments) {
    if (/^\d+$/.test(seg)) {
      const n = parseInt(seg, 10);
      if (n <= 0) throw new DurationParseError("non_positive", { seg });
      if (n > MAX_SINGLE_VALUE) {
        throw new DurationParseError("exceeds_max", { seg, max: MAX_SINGLE_VALUE });
      }
      result.add(n);
      continue;
    }
    const m = /^(\d+)-(\d+)$/.exec(seg);
    if (m) {
      const lo = parseInt(m[1], 10);
      const hi = parseInt(m[2], 10);
      if (lo <= 0 || hi <= 0) throw new DurationParseError("non_positive", { seg });
      if (hi < lo) throw new DurationParseError("range_inverted", { seg });
      if (hi - lo > MAX_RANGE_SPAN) {
        throw new DurationParseError("range_too_large", { seg, max_span: MAX_RANGE_SPAN });
      }
      if (hi > MAX_SINGLE_VALUE) {
        throw new DurationParseError("exceeds_max", { seg, max: MAX_SINGLE_VALUE });
      }
      for (let i = lo; i <= hi; i++) result.add(i);
      continue;
    }
    throw new DurationParseError("unparseable", { seg });
  }

  return [...result].sort((a, b) => a - b);
}

/** Whether lists contain consecutive integers (e.g. [3,4,5,6,7]), requiring ≥2 elements. */
export function isContinuousIntegerRange(durations: readonly number[]): boolean {
  if (durations.length < 2) return false;
  const sorted = [...durations].sort((a, b) => a - b);
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i] !== sorted[i - 1] + 1) return false;
  }
  return true;
}

/**
 * Compact list[int] displays, collapsing consecutive runs to "min-max".
 *
 * Example: [3,4,5,7,8,9,10,12] → "3-5, 7-10, 12"
 */
export function compactRangeFormat(durations: readonly number[]): string {
  if (durations.length === 0) return "";
  const sorted = [...new Set(durations)].sort((a, b) => a - b);
  const parts: string[] = [];
  let runStart = sorted[0];
  let runPrev = sorted[0];
  for (let i = 1; i < sorted.length; i++) {
    const v = sorted[i];
    if (v === runPrev + 1) {
      runPrev = v;
    } else {
      parts.push(runStart === runPrev ? `${runStart}` : `${runStart}-${runPrev}`);
      runStart = v;
      runPrev = v;
    }
  }
  parts.push(runStart === runPrev ? `${runStart}` : `${runStart}-${runPrev}`);
  return parts.join(", ");
}

/** UI labels: consecutive ranges → "3-7s"; otherwise → "4, 6, 8s". */
export function formatDurationsLabel(durations: readonly number[]): string {
  if (durations.length === 0) return "";
  if (isContinuousIntegerRange(durations)) {
    const sorted = [...durations].sort((a, b) => a - b);
    return `${sorted[0]}-${sorted[sorted.length - 1]}s`;
  }
  return `${[...durations].sort((a, b) => a - b).join(", ")}s`;
}
