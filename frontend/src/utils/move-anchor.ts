
export function dropAnchor<T>(ids: readonly T[], source: number, target: number): T | null | undefined {
  if (source === target || source < 0 || target < 0) return undefined;
  if (source < target) return ids[target];
  return target > 0 ? ids[target - 1] : null;
}

export function stepAnchor<T>(
  ids: readonly T[],
  index: number,
  direction: "earlier" | "later",
): T | null | undefined {
  if (index < 0 || index >= ids.length) return undefined;
  if (direction === "earlier") return index > 0 ? (ids[index - 2] ?? null) : undefined;
  return index < ids.length - 1 ? ids[index + 1] : undefined;
}
