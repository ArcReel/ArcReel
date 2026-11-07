export interface GridLayout {
  gridSize: "grid_4" | "grid_9" | "grid_16" | "grid_25" | null;
  rows: number;
  cols: number;
  cellCount: number;
  batchCount: number;
}

/**
 * Tier ladders match backend lib/script/grid/layout.py _GRID_LADDER itemwise:
 * every layout is N×N, with cell ratios identical to full images/project video ratios.
 * Available tiers follow backend /grid-capability max_cell_count
 * (4K gating needs provider resolution; frontend never infers resolution tiers).
 */
const GRID_LADDER = [
  { cellCount: 4, gridSize: "grid_4", side: 2 },
  { cellCount: 9, gridSize: "grid_9", side: 3 },
  { cellCount: 16, gridSize: "grid_16", side: 4 },
  { cellCount: 25, gridSize: "grid_25", side: 5 },
] as const;

/** Missing backend limits present gated maximum 3×3, preferring undercounted batches over false claims. */
const FALLBACK_MAX_CELL_COUNT = 9;

interface GridMatchRecord {
  id: string;
  episode: number;
  scene_ids: string[];
  created_at: string;
}

/**
 * Match grids to current group chunks, sharing backend grid_submission rules:
 * partition groups by computeGridSize cell counts; grids match only identical scene_ids
 * including order, choosing latest created_at for repeated generations.
 * Changed chapter boundaries/order/membership change chunks; unmatched old grids show ungenerated chunks.
 * Return chunk-ordered matches, without placeholders for absent grids.
 */
export function matchGridsForGroup<G extends GridMatchRecord>(
  grids: G[],
  groupSceneIds: readonly string[],
  episode: number,
  maxCellCount?: number,
): G[] {
  const { cellCount } = computeGridSize(groupSceneIds.length, maxCellCount);
  const latestByChunk = new Map<string, G>();
  for (const g of grids) {
    if (g.episode !== episode) continue;
    const key = g.scene_ids.join("\u0000");
    const current = latestByChunk.get(key);
    if (!current || g.created_at.localeCompare(current.created_at) > 0) latestByChunk.set(key, g);
  }
  const matched: G[] = [];
  for (let start = 0; cellCount > 0 && start < groupSceneIds.length; start += cellCount) {
    const chunk = groupSceneIds.slice(start, start + cellCount);
    const g = latestByChunk.get(chunk.join("\u0000"));
    if (g) matched.push(g);
  }
  return matched;
}

export function groupBySegmentBreak<S extends { segment_break?: boolean }>(
  segments: S[],
): S[][] {
  const groups: S[][] = [];
  let current: S[] = [];
  for (const seg of segments) {
    if (seg.segment_break && current.length > 0) {
      groups.push(current);
      current = [];
    }
    current.push(seg);
  }
  if (current.length > 0) groups.push(current);
  return groups;
}

export function computeGridSize(
  count: number,
  maxCellCount: number = FALLBACK_MAX_CELL_COUNT
): GridLayout {
  if (count < 1) return { gridSize: null, rows: 0, cols: 0, cellCount: 0, batchCount: 0 };
  const effective = Math.min(count, maxCellCount);
  const { cellCount, gridSize, side } =
    GRID_LADDER.find((cfg) => effective <= cfg.cellCount) ?? GRID_LADDER[GRID_LADDER.length - 1];

  const batchCount = count > cellCount ? Math.ceil(count / cellCount) : 1;
  return { gridSize, rows: side, cols: side, cellCount, batchCount };
}
