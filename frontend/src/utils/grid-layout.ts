export interface GridLayout {
  gridSize: "grid_4" | "grid_9" | "grid_16" | "grid_25" | null;
  rows: number;
  cols: number;
  cellCount: number;
  batchCount: number;
}

/**
 * 档位阶梯，与后端 lib/grid/layout.py 的 _GRID_LADDER 逐项对应:
 * 全部为 N×N 平方切分,单格比例恒等于整图比例(即项目视频比例)。
 * 哪几档可用由格数上限决定,上限取自后端 /grid-capability 的 max_cell_count
 * (4K 门控要经供应商解析才能定,前端不自行推导分辨率档)。
 */
const GRID_LADDER = [
  { cellCount: 4, gridSize: "grid_4", side: 2 },
  { cellCount: 9, gridSize: "grid_9", side: 3 },
  { cellCount: 16, gridSize: "grid_16", side: 4 },
  { cellCount: 25, gridSize: "grid_25", side: 5 },
] as const;

/** 拿不到后端上限时按门控生效展示(封顶 3×3),宁可少算批次也不虚报 */
export const FALLBACK_MAX_CELL_COUNT = 9;

interface GridMatchRecord {
  id: string;
  episode: number;
  scene_ids: string[];
  created_at: string;
}

/**
 * 后端会把超过 layout.cell_count 的 group 拆成多个 chunk,
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
