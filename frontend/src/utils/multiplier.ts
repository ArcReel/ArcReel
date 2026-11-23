const DECIMAL_RE = /^(?:\d+(?:\.\d*)?|\.\d+)$/;

export function parseMultiplier(s: string): number | undefined {
  const trimmed = s.trim();
  if (!DECIMAL_RE.test(trimmed)) return undefined;
  const n = Number(trimmed);
  return Number.isFinite(n) && n >= 0 ? n : undefined;
}
