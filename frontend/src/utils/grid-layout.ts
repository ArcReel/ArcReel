export interface GridLayout {
  gridSize: "grid_4" | "grid_6" | "grid_9" | null;
  rows: number;
  cols: number;
  cellCount: number;
  batchCount: number;
}

interface GridMatchRecord {
  id: string;
  episode: number;
  scene_ids: string[];
  created_at: string;
}

/**
 * 后端会把超过 layout.cell_count(最多 9)的 group 拆成多个 chunk,
 * 每条 grid 记录的 scene_ids 是 group 的子集。匹配时按子集判断,
 * 同一组 scene_ids 取 created_at 最新的一条,再按 created_at 升序返回。
 */
export function matchGridsForGroup<G extends GridMatchRecord>(
  grids: G[],
  groupSceneIds: Iterable<string>,
  episode: number,
): G[] {
  const idSet = new Set(groupSceneIds);
  const matched = grids.filter(
    (g) =>
      g.episode === episode &&
      g.scene_ids.length > 0 &&
      g.scene_ids.every((id) => idSet.has(id)),
  );
  const byKey = new Map<string, G>();
  for (const g of matched) {
    const key = [...g.scene_ids].sort().join(",");
    const existing = byKey.get(key);
    if (!existing || g.created_at > existing.created_at) {
      byKey.set(key, g);
    }
  }
  return Array.from(byKey.values()).sort((a, b) =>
    a.created_at.localeCompare(b.created_at),
  );
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

export function computeGridSize(count: number, aspectRatio: string = "9:16"): GridLayout {
  if (count < 1) return { gridSize: null, rows: 0, cols: 0, cellCount: 0, batchCount: 0 };
  const [w, h] = aspectRatio.split(":").map(Number);
  const isHorizontal = w > h;
  const effective = Math.min(count, 9);

  let gridSize: "grid_4" | "grid_6" | "grid_9";
  let cellCount: number;
  let rows: number;
  let cols: number;

  if (effective <= 4) {
    gridSize = "grid_4";
    cellCount = 4;
    rows = 2;
    cols = 2;
  } else if (effective <= 6) {
    gridSize = "grid_6";
    cellCount = 6;
    rows = isHorizontal ? 3 : 2;
    cols = isHorizontal ? 2 : 3;
  } else {
    gridSize = "grid_9";
    cellCount = 9;
    rows = 3;
    cols = 3;
  }

  const batchCount = count > cellCount ? Math.ceil(count / cellCount) : 1;
  return { gridSize, rows, cols, cellCount, batchCount };
}
