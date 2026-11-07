/**
 * Reorder-anchor conversion: backend move places entries after an anchor, or first when the anchor is null.
 * Return undefined if this operation does not move anything.
 */

/** Drop source onto target: downward moves land after target, upward moves before it, meaning after the preceding entry. */
export function dropAnchor<T>(ids: readonly T[], source: number, target: number): T | null | undefined {
  if (source === target || source < 0 || target < 0) return undefined;
  if (source < target) return ids[target];
  return target > 0 ? ids[target - 1] : null;
}

/** Move one position earlier/later: earlier lands after the entry two positions before (second moves to first); later lands after the next entry. */
export function stepAnchor<T>(
  ids: readonly T[],
  index: number,
  direction: "earlier" | "later",
): T | null | undefined {
  if (index < 0 || index >= ids.length) return undefined;
  if (direction === "earlier") return index > 0 ? (ids[index - 2] ?? null) : undefined;
  return index < ids.length - 1 ? ids[index + 1] : undefined;
}
