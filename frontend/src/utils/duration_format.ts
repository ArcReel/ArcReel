
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

export function isContinuousIntegerRange(durations: readonly number[]): boolean {
  if (durations.length < 2) return false;
  const sorted = [...durations].sort((a, b) => a - b);
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i] !== sorted[i - 1] + 1) return false;
  }
  return true;
}

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

export function formatDurationsLabel(durations: readonly number[]): string {
  if (durations.length === 0) return "";
  if (isContinuousIntegerRange(durations)) {
    const sorted = [...durations].sort((a, b) => a - b);
    return `${sorted[0]}-${sorted[sorted.length - 1]}s`;
  }
  return `${[...durations].sort((a, b) => a - b).join(", ")}s`;
}
