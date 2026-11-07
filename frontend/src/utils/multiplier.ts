// Multiplier: a nonnegative finite number. Accept only decimal notation (1.5, 0.8, .5); scientific notation, negative signs and all other characters
// are invalid, returning undefined to block save, rather than silently truncating through parseFloat (1.5x → 1.5).
const DECIMAL_RE = /^(?:\d+(?:\.\d*)?|\.\d+)$/;

/** Required multiplier (provider-model or Agent-credential multiplier): return a valid number, or undefined for empty/invalid input. */
export function parseMultiplier(s: string): number | undefined {
  const trimmed = s.trim();
  if (!DECIMAL_RE.test(trimmed)) return undefined;
  const n = Number(trimmed);
  return Number.isFinite(n) && n >= 0 ? n : undefined;
}
