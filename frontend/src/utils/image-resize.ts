/**
 * Reproduce Anthropic official references: models resize to largest proportional dimensions satisfying both
 * edge-length/vision-token limits. See https://platform.claude.com/docs/en/build-with-claude/vision-coordinates
 */

/** Vision tokens: one token per 28×28 pixel block. */
function countImageTokens(width: number, height: number): number {
  return Math.ceil(width / 28) * Math.ceil(height / 28);
}

/**
 * Half-even banker's rounding, matching Python `round()`.
 * Server exactly-.5 short edges choose even neighbors; `Math.round` always rounds upward,
 * differing by one pixel for some images.
 */
function roundTiesToEven(value: number): number {
  const floor = Math.floor(value);
  if (value - floor !== 0.5) return Math.round(value);
  return floor % 2 === 0 ? floor : floor + 1;
}

/**
 * Model-resized pixel dimensions before padding, returning `[width, height]`.
 *
 * Defaults use standard resolution tiers. Images satisfying both limits return unchanged.
 */
export function resizedSize(
  width: number,
  height: number,
  maxEdge = 1568,
  maxTokens = 1568,
): [number, number] {
  const fits = (w: number, h: number): boolean =>
    Math.ceil(w / 28) * 28 <= maxEdge
    && Math.ceil(h / 28) * 28 <= maxEdge
    && countImageTokens(w, h) <= maxTokens;

  if (fits(width, height)) return [width, height];
  if (height > width) {
    const [resizedH, resizedW] = resizedSize(height, width, maxEdge, maxTokens);
    return [resizedW, resizedH];
  }

  // Binary search along long edges for largest valid proportional sizes.
  const aspectRatio = width / height;
  let lo = 1; // lo always valid.
  let hi = width; // hi always invalid.
  while (lo + 1 < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (fits(mid, Math.max(roundTiesToEven(mid / aspectRatio), 1))) {
      lo = mid;
    } else {
      hi = mid;
    }
  }
  return [lo, Math.max(roundTiesToEven(lo / aspectRatio), 1)];
}
