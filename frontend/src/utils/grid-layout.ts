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
 * 再按 created_at 降序贪心覆盖:只保留贡献新 scene_id 的 grid,
 * 过滤掉被新生成覆盖的旧 chunk(用户调整 segment_break 后未重新生成时,
 * 旧 chunk 仍在 grids 表里但已不属于当前布局)。
 * 返回时按 created_at 升序,保证 batch pills 显示顺序稳定。
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

  const sorted = [...matched].sort((a, b) =>
    b.created_at.localeCompare(a.created_at),
  );

  const selected: G[] = [];
  const covered = new Set<string>();
  for (const g of sorted) {
    const hasUncovered = g.scene_ids.some((id) => !covered.has(id));
    if (hasUncovered) {
      selected.push(g);
      for (const id of g.scene_ids) covered.add(id);
    }
  }

  return selected.sort((a, b) => a.created_at.localeCompare(b.created_at));
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
